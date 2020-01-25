import os
import json
import torch
import random
import pickle
import skfmm
import numpy as np
from evaluation import *
from functions import *
import torch.optim as optim
from torch.utils.data import TensorDataset
from Models.AutoEncoder import AutoEncoder
from Models.AutoEncoderEx import AutoEncoderEx
from Models.UNet import UNet
from Models.UNetEx import UNetEx
from matplotlib import pyplot as plt

if __name__ == "__main__":
    results_directory = "Results/"
    if not os.path.exists(results_directory):
        os.makedirs(results_directory)

    models = [AutoEncoder, AutoEncoderEx, UNet, UNetEx]
    learning_rates = [1e-3, 1e-4, 1e-5]
    kernel_sizes = [3, 5, 7]
    filters = [[16, 32, 64], [8, 16, 32, 32], [8, 16, 16, 32, 32]]
    batch_norm = [False, True]
    weight_norm = [False, True]
    weight_decay = [0.005]

    # Loading dataset
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    x = pickle.load(open("./Xs.pkl", "rb"))
    y = pickle.load(open("./Ys.pkl", "rb"))
    x = torch.FloatTensor(x)
    y = torch.FloatTensor(y)
    # Shifting dimensions
    x, y = x.permute(0, 3, 1, 2), y.permute(0, 3, 1, 2)
    # Removing channel 1 in input channels
    x = torch.cat((x[:, 0:1, :, :], x[:, 2:, :, :]), dim=1)
    # Adding binary channel to describe the shape of object
    mask = (1 - torch.isnan(x[:, 2:, :, :]).type(torch.float))
    mask[:, 0, 0, :] = 3
    mask[:, 0, mask.shape[2]-1, :] = 4
    mask[:, 0, :, 0] = 2
    mask[:, 0, :, mask.shape[3]-1] = 2
    x = torch.cat((x[:, 0:1, :, :], mask), dim=1)
    # Adding sdf from walls
    phi = -1*np.ones(x[0, 0, :, :].shape)
    phi = phi.reshape((1, 1, phi.shape[0], phi.shape[1]))
    sdf = np.array(phi, copy=True)
    sdf[0, 0, :, 0] = 1
    sdf[0, 0, :, 78] = 1
    d = skfmm.distance(-sdf, dx = 1e-2)
    d = np.repeat(d, x.shape[0], axis=0)
    d = torch.FloatTensor(d)
    x = torch.cat((x, d), dim=1)
    x[torch.isnan(x)] = 0
    y[torch.isnan(y)] = 0
    channels_weights = torch.sqrt(torch.mean(y.permute(0, 2, 3, 1).view(-1, y.shape[1]) ** 2, dim=0)).view(1, -1, 1, 1).to(device)
    print(channels_weights)
    print(x.shape)
    print(y.shape)
    # Shuffling the dataset
    x, y = shuffle_tensors(x, y)
    # Spliting dataset into 70% train and 30% test
    train_data, test_data = split_tensors(x, y, ratio=0.7)
    train_dataset, test_dataset = TensorDataset(*train_data), TensorDataset(*test_data)

    test_sample_x, test_sample_y = test_dataset[random.randint(0, len(test_dataset))]
    test_sample_x, test_sample_y = test_sample_x.unsqueeze(0).to(device), test_sample_y.unsqueeze(0).to(device)

    def train_cnnCFD(config):
        print("Evaluating configuration: ")
        print(config)
        torch.manual_seed(0)
        model = config["model"]
        lr = config["lr"]
        kernel_size = config["kernel"]
        filters = config["filters"]
        bn = config["bn"]
        wn = config["wn"]
        wd = config["wd"]
        model = model(3, 3, filters=filters, kernel_size=kernel_size,
                    batch_norm=bn, weight_norm=wn)
        optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=wd)

        train_loss_curve = []
        test_loss_curve = []
        train_mse_curve = []
        test_mse_curve = []
        train_ux_curve = []
        test_ux_curve = []
        train_uy_curve = []
        test_uy_curve = []
        train_p_curve = []
        test_p_curve = []

        def after_epoch(scope):
            train_loss_curve.append(scope["train_loss"])
            test_loss_curve.append(scope["val_loss"])
            train_mse_curve.append(scope["train_metrics"]["mse"])
            test_mse_curve.append(scope["val_metrics"]["mse"])
            train_ux_curve.append(scope["train_metrics"]["ux"])
            test_ux_curve.append(scope["val_metrics"]["ux"])
            train_uy_curve.append(scope["train_metrics"]["uy"])
            test_uy_curve.append(scope["val_metrics"]["uy"])
            train_p_curve.append(scope["train_metrics"]["p"])
            test_p_curve.append(scope["val_metrics"]["p"])

        def loss_func(model, batch):
            x, y = batch
            output = model(x)
            lossu = ((output[:,0,:,:] - y[:,0,:,:]) ** 2).reshape((output.shape[0],1,output.shape[2],output.shape[3])) #/ channels_weights
            lossv = ((output[:,1,:,:] - y[:,1,:,:]) ** 2).reshape((output.shape[0],1,output.shape[2],output.shape[3])) #/ channels_weights
            lossp = torch.abs((output[:,2,:,:] - y[:,2,:,:])).reshape((output.shape[0],1,output.shape[2],output.shape[3])) #/ channels_weights
            loss = (lossu + lossv + lossp)/channels_weights
            return torch.sum(loss), output
        
        # Training model
        best_model, train_metrics, train_loss, test_metrics, test_loss = train_model(model, loss_func, train_dataset, test_dataset, optimizer,
                epochs=10000, batch_size=32, device=device,
                m_mse_name="Total MSE",
                m_mse_on_batch=lambda scope: float(torch.sum((scope["output"] - scope["batch"][1]) ** 2)),
                m_mse_on_epoch=lambda scope: sum(scope["list"]) / len(scope["dataset"]),
                m_ux_name="Ux MSE",
                m_ux_on_batch=lambda scope: float(torch.sum((scope["output"][:,0,:,:] - scope["batch"][1][:,0,:,:]) ** 2)),
                m_ux_on_epoch=lambda scope: sum(scope["list"]) / len(scope["dataset"]),
                m_uy_name="Uy MSE",
                m_uy_on_batch=lambda scope: float(torch.sum((scope["output"][:,1,:,:] - scope["batch"][1][:,1,:,:]) ** 2)),
                m_uy_on_epoch=lambda scope: sum(scope["list"]) / len(scope["dataset"]),
                m_p_name="p MSE",
                m_p_on_batch=lambda scope: float(torch.sum((scope["output"][:,2,:,:] - scope["batch"][1][:,2,:,:]) ** 2)),
                m_p_on_epoch=lambda scope: sum(scope["list"]) / len(scope["dataset"]), patience=25, after_epoch=after_epoch
                )
        # Saving results
        simulation_directory = results_directory + str(config["id"]).zfill(6) + "/"
        if not os.path.exists(simulation_directory):
            os.makedirs(simulation_directory)
        metrics = {}
        metrics["train_metrics"] = train_metrics
        metrics["train_loss"] = train_loss
        metrics["test_metrics"] = test_metrics
        metrics["test_loss"] = test_loss
        curves = {}
        curves["train_loss_curve"] = train_loss_curve
        curves["test_loss_curve"] = test_loss_curve
        curves["train_mse_curve"] = train_mse_curve
        curves["test_mse_curve"] = test_mse_curve
        curves["train_ux_curve"] = train_ux_curve
        curves["test_ux_curve"] = test_ux_curve
        curves["train_uy_curve"] = train_uy_curve
        curves["test_uy_curve"] = test_uy_curve
        curves["train_p_curve"] = train_p_curve
        curves["test_p_curve"] = test_p_curve
        config["model"] = str(config["model"])
        config["metrics"] = metrics
        config["curves"] = curves
        with open(simulation_directory + "results.json", "w") as file:
            json.dump(config, file)
        # Plotting curves
        plt.figure()
        plt.plot(train_loss_curve, "-r", label='Train')
        plt.plot(test_loss_curve, "-g", label='Validation')
        plt.legend()
        plt.savefig(simulation_directory + "loss.png", bbox_inches='tight')
        plt.close()
        plt.figure()
        plt.plot(train_mse_curve, "-r", label='Train')
        plt.plot(test_mse_curve, "-g", label='Validation')
        plt.legend()
        plt.savefig(simulation_directory + "mse.png", bbox_inches='tight')
        plt.close()
        plt.figure()
        plt.plot(train_ux_curve, "-r", label='Train')
        plt.plot(test_ux_curve, "-g", label='Validation')
        plt.legend()
        plt.savefig(simulation_directory + "ux.png", bbox_inches='tight')
        plt.close()
        plt.figure()
        plt.plot(train_uy_curve, "-r", label='Train')
        plt.plot(test_uy_curve, "-g", label='Validation')
        plt.legend()
        plt.savefig(simulation_directory + "uy.png", bbox_inches='tight')
        plt.close()
        plt.figure()
        plt.plot(train_p_curve, "-r", label='Train')
        plt.plot(test_p_curve, "-g", label='Validation')
        plt.legend()
        plt.savefig(simulation_directory + "p.png", bbox_inches='tight')
        plt.close()
        # Plotting results
        with torch.no_grad():
            test_sample_out = best_model(test_sample_x)
        visualize(test_sample_y.cpu().numpy(), test_sample_out.cpu().numpy())
        plt.savefig(simulation_directory + "output.png", bbox_inches='tight')
        plt.close()
        torch.save(best_model, simulation_directory + "model")
        print("Best loss = " + str(test_loss))
        return test_loss
    simulation_id = 0
    best_loss = float("inf")
    best_config = None
    for model in models:
        for kernel in kernel_sizes:
            for filter in filters:
                for bn in batch_norm:
                    for wn in weight_norm:
                        for lr in learning_rates:
                            for wd in weight_decay:
                                config = {
                                    "id" : simulation_id,
                                    "model" : model,
                                    "lr" : lr,
                                    "kernel" : kernel,
                                    "filters" : filter,
                                    "bn" : bn,
                                    "wn" : wn,
                                    "wd" : wd,
                                }
                                loss = train_cnnCFD(config)
                                if loss < best_loss:
                                    best_loss = loss
                                    best_config = config
                                simulation_id += 1
    print("Best configuration: ")
    print(best_config)
    print("Minimum loss = " + str(best_loss))
    with open(results_directory + "best_config.json", "w") as file:
        json.dump(best_config, file)
