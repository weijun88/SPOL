#coding=utf-8
import os
import sys
import datetime
import argparse
import numpy as np
import matplotlib.pyplot as plt
plt.ion()
sys.path.insert(0, '../')
sys.dont_write_bytecode = True
os.environ["CUDA_VISIBLE_DEVICES"] = '0'

import cv2
import apex
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
from torch.utils.tensorboard import SummaryWriter
from model import Model
import albumentations as A
from albumentations.pytorch import ToTensorV2

class Data(Dataset):
    def __init__(self, args):
        self.args      = args
        self.transform = A.Compose([
            A.Normalize(),
            A.Resize(256, 256),
            A.RandomCrop(224, 224),
            A.HorizontalFlip(p=0.5),
            ToTensorV2()
        ])

        self.samples = []
        with open(args.datapath+'/'+args.list, 'r') as lines:
            for line in lines:
                name, label = line.strip().split(' ')
                self.samples.append([name, int(label)])

    def __getitem__(self, idx):
        name, label      = self.samples[idx]
        image            = cv2.imread(self.args.datapath+'/train/'+name)
        image            = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        mask             = cv2.imread(self.args.datapath+'/seg/'+name)/255.0
        pair             = self.transform(image=image, mask=mask)
        image, mask      = pair['image'], pair['mask']
        fore, gaus, back = mask[:,:,0], mask[:,:,1], mask[:,:,2]
        mask             = torch.maximum(fore>0.25, gaus>0.25)
        weight           = torch.maximum(mask, fore<0.01)
        return image, mask, weight

    def __len__(self):
        return len(self.samples)
        

class Train(object):
    def __init__(self, Data, Model, args):
        ## dataset
        self.args    = args 
        self.data    = Data(args)
        self.loader  = DataLoader(self.data, batch_size=args.batch_size, pin_memory=False, shuffle=True, num_workers=args.num_workers)
        ## model
        self.model = Model(args)
        self.model.train(True)
        self.model.cuda()
        ## parameter
        base, head = [], []
        for name, param in self.model.named_parameters():
            if 'bkbone' in name:
                base.append(param)
            else:
                head.append(param)
        self.optimizer = torch.optim.SGD([{'params':base, 'lr':self.args.lr}, {'params':head, 'lr':self.args.lr}], momentum=args.momentum, weight_decay=args.weight_decay, nesterov=args.nesterov)
        self.model, self.optimizer = apex.amp.initialize(self.model, self.optimizer, opt_level='O2')
        self.logger = SummaryWriter(args.savepath)

    def train(self):
        global_step = 0
        for epoch in range(self.args.epoch):
            for image, mask, weight in self.loader:
                image, mask, weight = image.cuda().float(), mask.cuda().float(), weight.cuda().float()
                B,H,W = mask.size()
                pred  = self.model(image)
                pred  = F.interpolate(pred, (H, W), mode='bilinear', align_corners=True)[:, 0, :, :]
                loss  = F.binary_cross_entropy_with_logits(pred, mask, weight=weight)
                self.optimizer.zero_grad()
                with apex.amp.scale_loss(loss, self.optimizer) as scale_loss:
                    scale_loss.backward()
                self.optimizer.step()

                ## log
                global_step += 1
                self.logger.add_scalar('lr'  , self.optimizer.param_groups[0]['lr'], global_step=global_step)
                self.logger.add_scalar('loss', loss.item(), global_step=global_step)
                if global_step % 10 == 0:
                    print('%s | step:%d/%d/%d | lr=%.6f | loss=%.6f'%(datetime.datetime.now(), global_step, epoch+1, self.args.epoch, self.optimizer.param_groups[0]['lr'], loss.item()))
                if global_step % 2000 == 0:
                    torch.save(self.model.state_dict(), self.args.savepath+'/model-'+str(global_step))


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--datapath'    ,default='../dataset/ImageNet2012')
    parser.add_argument('--savepath'    ,default='./out')
    parser.add_argument('--mode'        ,default='train')
    parser.add_argument('--list'        ,default='train.txt')
    parser.add_argument('--lr'          ,default=0.02)
    parser.add_argument('--epoch'       ,default=1)
    parser.add_argument('--batch_size'  ,default=128)
    parser.add_argument('--weight_decay',default=1e-4)
    parser.add_argument('--momentum'    ,default=0.9)
    parser.add_argument('--nesterov'    ,default=True)
    parser.add_argument('--num_workers' ,default=8)
    parser.add_argument('--snapshot'    ,default=None)
    args = parser.parse_args()

    torch.backends.cudnn.enabled = True
    t = Train(Data, Model, args)
    t.train()
