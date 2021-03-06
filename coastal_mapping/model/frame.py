#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Sep  4 22:59:16 2020

@author: mibook

Frame to Combine Model with Optimizer

This wraps the model and optimizer objects needed in training, so that each
training step can be concisely called with a single method.
"""
from pathlib import Path
import torch
import numpy as np
from torch.optim.lr_scheduler import ReduceLROnPlateau
import os
from .metrics import *
from .unet import *
import pdb

class Framework:
    """
    Class to Wrap all the Training Steps

    """

    def __init__(self, loss_fn, model_opts=None, optimizer_opts=None,
                 reg_opts=None, device=None):
        """
        Set Class Attrributes
        """
        if device is None:
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        else:
            self.device = device
        if optimizer_opts is None:
            optimizer_opts = {"name": "Adam", "args": {"lr": 0.001}}
        self.multi_class = True if model_opts.args.outchannels > 1 else False
        self.num_classes = model_opts.args.outchannels    
        self.loss_fn = loss_fn.to(self.device)
        self.model = Unet(**model_opts.args).to(self.device)
        optimizer_def = getattr(torch.optim, optimizer_opts["name"])
        self.optimizer = optimizer_def(self.model.parameters(), **optimizer_opts["args"])
        self.lrscheduler = ReduceLROnPlateau(self.optimizer, "min",
                                             verbose=True, patience=5,
                                             min_lr=1e-6)
        self.reg_opts = reg_opts


    def optimize(self, x, y):
        """
        Take a single gradient step

        Args:
            X: raw training data
            y: labels
        Return:
            optimization
        """
        x = x.permute(0, 3, 1, 2).to(self.device)
        y = y.permute(0, 3, 1, 2).to(self.device)

        self.optimizer.zero_grad()
        y_hat = self.model(x)
        
        loss = self.calc_loss(y_hat, y)
        loss.backward()
        self.optimizer.step()
        return y_hat.permute(0, 2, 3, 1), loss.item()

    def val_operations(self, val_loss):
        """
        Update the LR Scheduler
        """
        self.lrscheduler.step(val_loss)

    def save(self, out_dir, epoch):
        """
        Save a model checkpoint
        """
        if not os.path.exists(out_dir):
            os.makedirs(out_dir)
        model_path = Path(out_dir, f"model_{epoch}.pt")
        optim_path = Path(out_dir, f"optim_{epoch}.pt")
        torch.save(self.model.state_dict(), model_path)
        torch.save(self.optimizer.state_dict(), optim_path)

    def infer(self, x):
        """ Make a prediction for a given x

        Args:
            x: input x

        Return:
            Prediction

        """
        x = x.permute(0, 3, 1, 2).to(self.device)
        with torch.no_grad():
            return self.model(x).permute(0, 2, 3, 1)

    def calc_loss(self, y_hat, y):
        """ Compute loss given a prediction

        Args:
            y_hat: Prediction
            y: Label

        Return:
            Loss values

        """
        y_hat = y_hat.to(self.device)
        y = y.to(self.device)
        loss = self.loss_fn(y_hat, y)
        for reg_type in self.reg_opts.keys():
            reg_fun = globals()[reg_type]
            penalty = reg_fun(
                self.model.parameters(),
                self.reg_opts[reg_type],
                self.device
            )
            loss += penalty

        return loss


    def metrics(self, y_hat, y, metrics_opts, masked):
        """ Loop over metrics in train.yaml

        Args:
            y_hat: Predictions
            y: Labels
            metrics_opts: Metrics specified in the train.yaml

        Return:
            results

        """
        y_hat = y_hat.to(self.device)
        y = y.to(self.device)

        if masked:
            mask = torch.sum(y, dim=3) == 1
            y_hat[mask] == torch.zeros(y.shape[3]).to(self.device)

        results = {}
        for k, metric in metrics_opts.items():
            if "threshold" in metric.keys():
                metric_value = torch.zeros(y_hat.shape[3])
                y_hat = y_hat > metric["threshold"]
                metric_fun = globals()[k]
                for i in range(y_hat.shape[3]):
                    metric_value[i] = metric_fun(y_hat[:,:,:,i], y[:,:,:,i])
            results[k] = metric_value
            
        return results
    
    def segment(self, y_hat):
        """Predict a class given logits
        Args:
            y_hat: logits output
        Return:
            Probability of class in case of binary classification
            or one-hot tensor in case of multi class"""
        if self.multi_class:
            y_hat = torch.argmax(y_hat, axis=3)
            y_hat = torch.nn.functional.one_hot(y_hat, num_classes=self.num_classes)
        else:
            y_hat = torch.sigmoid(y_hat)
            
        return y_hat
    
    def act(self, logits):
        """Applies activation function based on the model
        Args:
            y_hat: logits output
        Returns:
            logits after applying activation function"""

        if self.multi_class:
            y_hat = torch.nn.Softmax(3)(logits)
        else:
            y_hat = torch.sigmoid(logits)
        return y_hat

    def load_state_dict(self, state_dict):
        self.model.load_state_dict(state_dict)