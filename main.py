from __future__ import print_function
import os
import argparse
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.optim.lr_scheduler import CosineAnnealingLR
from data import ModelNet40
from model import FM3D, DGCNN, contrastive_loss
import numpy as np
from torch.utils.data import DataLoader
import sklearn.metrics as metrics
from time import time
import matplotlib.pyplot as plt
import numpy as np
from torchsummary import summary


def _init_():
    if not os.path.exists('checkpoints'):
        os.makedirs('checkpoints')
    if not os.path.exists('checkpoints/'+args.exp_name):
        os.makedirs('checkpoints/'+args.exp_name)
    if not os.path.exists('checkpoints/'+args.exp_name+'/'+'models'):
        os.makedirs('checkpoints/'+args.exp_name+'/'+'models')
    os.system('cp main.py checkpoints'+'/'+args.exp_name+'/'+'main.py.backup')
    os.system('cp model.py checkpoints' + '/' + args.exp_name + '/' + 'model.py.backup')
    os.system('cp util.py checkpoints' + '/' + args.exp_name + '/' + 'util.py.backup')
    os.system('cp data.py checkpoints' + '/' + args.exp_name + '/' + 'data.py.backup')

def train(args):
    train_loader = DataLoader(ModelNet40(partition='train', num_points=args.num_points, debug = args.debug), num_workers=8,
                              batch_size=args.batch_size, shuffle=True, drop_last=True)
    test_loader = DataLoader(ModelNet40(partition='test', num_points=args.num_points, debug = args.debug), num_workers=8,
                             batch_size=args.test_batch_size, shuffle=True, drop_last=False)

    device = torch.device("cuda" if args.cuda else "cpu")

    #Try to load models
    model = FM3D(args).to(device)
    model = nn.DataParallel(model)
    print("Let's use", torch.cuda.device_count(), "GPUs!")

    if args.use_sgd:
        print("Use SGD")
        opt = optim.SGD(model.parameters(), lr=args.lr*100, momentum=args.momentum, weight_decay=1e-4)
    else:
        print("Use Adam")
        opt = optim.Adam(model.parameters(), lr=args.lr, weight_decay=1e-4)

    scheduler = CosineAnnealingLR(opt, args.epochs, eta_min=args.lr)
    
    # optionally resume from a checkpoint
    # ximin
    if args.model_path:
        if os.path.isfile(args.model_path):
            print("=> loading checkpoint '{}'".format(args.model_path))
            checkpoint = torch.load(args.model_path)
            args.start_epoch = checkpoint['epoch']
            model.module.DGCNN.load_state_dict(checkpoint['DGCNN_state_dict'])
            model.module.predictor.load_state_dict(checkpoint['predictor_state_dict'])
            # model.load_state_dict(checkpoint['state_dict'])
            opt.load_state_dict(checkpoint['optimizer'])
            scheduler.load_state_dict(checkpoint['scheduler'])
            print("=> loaded checkpoint '{}' (epoch {})"
                  .format(args.model_path, checkpoint['epoch']))
        else:
            print("=> no checkpoint found at '{}'".format(args.model_path))

    loss_function = contrastive_loss(args).to(device)

    total_time = 0
    # ximin
    if 'start_epoch' in args:
        start_epoch = args.start_epoch + 1
    else:
        start_epoch = 0
    # train_loss_list = np.empty((0, 4)) ## 
    # train_count_list = []
    # total_count_train = 0
    # total_count_test = 0
    # test_loss_list = np.empty((0, 4))
    # test_count_list = []

    train_data = {
        "Final_loss":[],
        "FB_loss":[], 
        "M_loss1":[], 
        "M_loss2":[]
    }
    test_data = {
        "Final_loss":[],
        "FB_loss":[], 
        "M_loss1":[], 
        "M_loss2":[]
    }
    for epoch in range(start_epoch, args.epochs):
        count = 0
        epoch_time = 0
        model.train()
        train_epoch_data = {
            "Final_loss":0,
            "FB_loss":0, 
            "M_loss1":0, 
            "M_loss2":0
        }
        test_epoch_data = {
            "Final_loss":0,
            "FB_loss":0, 
            "M_loss1":0, 
            "M_loss2":0
        }
        for pointcloud, transformed_point_cloud in train_loader:
            t0 = time()
            pointcloud = pointcloud.to(device)  #b*1024*3
            transformed_point_cloud = transformed_point_cloud.to(device)
            pointcloud = pointcloud.permute(0, 2, 1) #b*3*1024
            transformed_point_cloud = transformed_point_cloud.permute(0, 2, 1)
            batch_size = pointcloud.size()[0]
            opt.zero_grad()
            fe1_nograd, fe2_nograd, fe1_final, fe2_final, M = model(pointcloud, transformed_point_cloud)
            final_loss, FB_loss, M_loss1,M_loss2 = loss_function(fe1_nograd, fe2_nograd, fe1_final, fe2_final, M)
            final_loss.backward()
            ## stack the lose into storge
            # train_loss_list = np.vstack((train_loss_list, np.array([final_loss.item(), FB_loss.item(), M_loss1.item(),M_loss2.item()])))
            opt.step()
            count += 1
            # total_count_train += batch_size
            # train_count_list.append(total_count_train)
            batch_time = time() - t0
            epoch_time += batch_time
            train_epoch_data["Final_loss"] += final_loss.item()
            train_epoch_data["FB_loss"] += FB_loss.item()
            train_epoch_data["M_loss1"] += M_loss1.item()
            train_epoch_data["M_loss2"] += M_loss2.item()
        train_data["Final_loss"].append(train_epoch_data["Final_loss"]/count)
        train_data["FB_loss"].append(train_epoch_data["FB_loss"]/count)
        train_data["M_loss1"].append(train_epoch_data["M_loss1"]/count)
        train_data["M_loss2"].append(train_epoch_data["M_loss2"]/count)
        scheduler.step()            
        outstr = 'Train epoch %d: final_loss: %.6f, FB_loss: %.6f, M_loss1: %.6f, M_loss2: %.6f, epoch training time: %.3f' \
                    % (epoch, train_data["Final_loss"][-1], train_data["FB_loss"][-1], train_data["M_loss1"][-1], train_data["M_loss2"][-1], epoch_time)     
        total_time += epoch_time
        print(outstr)
        checkpoint = {
            "DGCNN_state_dict": model.module.DGCNN.state_dict(), 
            "predictor_state_dict": model.module.predictor.state_dict(),
            "epoch": epoch,
            "optimizer": opt.state_dict(),
            "scheduler": scheduler.state_dict()
        }
        filename = 'checkpoints/'+args.exp_name+'/'+'models/'+f'{epoch}.pth'
        torch.save(checkpoint, filename)
        epoch_time = 0

        count = 0
        model.eval()
        with torch.no_grad():
            for pointcloud, transformed_point_cloud in test_loader:
                t0 = time()
                pointcloud = pointcloud.to(device)  # b*1024*3
                transformed_point_cloud = transformed_point_cloud.to(device)
                pointcloud = pointcloud.permute(0, 2, 1)  # b*3*1024
                transformed_point_cloud = transformed_point_cloud.permute(0, 2, 1)
                batch_size = pointcloud.size()[0]
                fe1_nograd, fe2_nograd, fe1_final, fe2_final, M = model(pointcloud, transformed_point_cloud)
                final_loss, FB_loss, M_loss1, M_loss2 = loss_function(fe1_nograd, fe2_nograd, fe1_final, fe2_final, M)
                # test_loss_list = np.vstack((test_loss_list, np.array([final_loss.item(), FB_loss.item(), M_loss1.item(),M_loss2.item()])))
                count += 1
                # total_count_test += batch_size
                # test_count_list.append(total_count_test)
                batch_time = time() - t0
                epoch_time += batch_time
                test_epoch_data["Final_loss"] += final_loss.item()
                test_epoch_data["FB_loss"] += FB_loss.item()
                test_epoch_data["M_loss1"] += M_loss1.item()
                test_epoch_data["M_loss2"] += M_loss2.item()
        test_data["Final_loss"].append(test_epoch_data["Final_loss"]/count)
        test_data["FB_loss"].append(test_epoch_data["FB_loss"]/count)
        test_data["M_loss1"].append(test_epoch_data["M_loss1"]/count)
        test_data["M_loss2"].append(test_epoch_data["M_loss2"]/count)
        outstr = 'Train epoch %d: final_loss: %.6f, FB_loss: %.6f, M_loss1: %.6f, M_loss2: %.6f, epoch training time: %.3f' \
                    % (epoch, test_data["Final_loss"][-1], test_data["FB_loss"][-1], test_data["M_loss1"][-1], test_data["M_loss2"][-1], epoch_time)     
        total_time += epoch_time
        print(outstr)  
        print("############################################################")
        # print('Finish epoch %d, training loss is: %.6f, testing loss is: %.6f, total time is: %.3f'\
        #         %(epoch, train_loss_list[-1, 0], test_loss_list[-1, 0], total_time))
        
        save_loss([train_data["Final_loss"], train_data["FB_loss"], train_data["M_loss1"], train_data["M_loss2"]],
                  [test_data["Final_loss"], test_data["FB_loss"], test_data["M_loss1"], test_data["M_loss2"]],
                  epoch+1
                  )
        print("Save loss figure.")
        print("############################################################")
        print("\n\n\n")
        

def save_loss(train_list, test_list, epoch):
    h = len(train_list)
    fig = plt.figure(figsize=(20, 20))
    loss_name = ["Final_loss", "FB_loss", "M_loss1", "M_loss2"]
    for i in range(h):
        ax = fig.add_subplot(h,2, 2 * i + 1)
        ax.plot(range(epoch), train_list[i])
        ax.set_title(loss_name[i] + ' for training')
        ax.set_xlabel("epoch")
        ax.set_ylabel("loss")
    for i in range(h):
        ax = fig.add_subplot(h,2, 2 * i + 2)
        ax.plot(range(epoch), test_list[i])
        ax.set_title(loss_name[i] + ' for testing')   
        ax.set_xlabel("epoch")     
        ax.set_ylabel("loss")
    # Save the full figure...
    if os.path.exists('Loss.jpg'):
        os.remove('Loss.jpg')
    fig.savefig('Loss.jpg')
    plt.close()

def test(args):  #not written
    # test_loader = DataLoader(ModelNet40(partition='test', num_points=args.num_points, debug=args.debug),
    #                          batch_size=args.test_batch_size, shuffle=True, drop_last=False)

    # device = torch.device("cuda" if args.cuda else "cpu")
    device = torch.device("cpu")

    #Try to load models
    model = DGCNN(args).to(device)

    print(summary(model, (24, 3, 1024) ) )

    exit(1)

    model = nn.DataParallel(model)
    # ximin
    checkpoint = torch.load(args.model_path, map_location=device )
    # model.module.load_state_dict(checkpoint['DGCNN_state_dict'])
    # model.load_state_dict(torch.load(args.model_path))

    print(checkpoint['state_dict'].keys() )

    model = model.eval()
    test_acc = 0.0
    count = 0.0
    test_true = []
    test_pred = []
    for data, label in test_loader:

        data, label = data.to(device), label.to(device).squeeze()
        data = data.permute(0, 2, 1)
        batch_size = data.size()[0]
        logits = model(data)
        preds = logits.max(dim=1)[1]
        test_true.append(label.cpu().numpy())
        test_pred.append(preds.detach().cpu().numpy())
    test_true = np.concatenate(test_true)
    test_pred = np.concatenate(test_pred)

    # ximin
    print(test_true.shape)
    print(test_pred.shape)

    test_acc = metrics.accuracy_score(test_true, test_pred)
    avg_per_class_acc = metrics.balanced_accuracy_score(test_true, test_pred)
    outstr = 'Test :: test acc: %.6f, test avg acc: %.6f'%(test_acc, avg_per_class_acc)


if __name__ == "__main__":
    # Training settings
    parser = argparse.ArgumentParser(description='Point Cloud Recognition')
    parser.add_argument('--exp_name', type=str, default='exp_use_exponential', metavar='N',
                        help='Name of the experiment')
    parser.add_argument('--model', type=str, default='dgcnn', metavar='N',
                        choices=['pointnet', 'dgcnn'],
                        help='Model to use, [pointnet, dgcnn]')
    parser.add_argument('--dataset', type=str, default='modelnet40', metavar='N',
                        choices=['modelnet40'])
    parser.add_argument('--batch_size', type=int, default=24, metavar='batch_size',
                        help='Size of batch)')
    parser.add_argument('--test_batch_size', type=int, default=24, metavar='batch_size',
                        help='Size of batch)')
    parser.add_argument('--epochs', type=int, default=200, metavar='N',
                        help='number of episode to train ')
    parser.add_argument('--use_sgd', type=bool, default=False,
                        help='Use SGD')
    parser.add_argument('--lr', type=float, default=0.001, metavar='LR',
                        help='learning rate (default: 0.001, 0.1 if using sgd)')
    parser.add_argument('--momentum', type=float, default=0.9, metavar='M',
                        help='SGD momentum (default: 0.9)')
    parser.add_argument('--no_cuda', type=bool, default=False,
                        help='enables CUDA training')
    parser.add_argument('--seed', type=int, default=1, metavar='S',
                        help='random seed (default: 1)')
    parser.add_argument('--eval', type=bool,  default=False,
                        help='evaluate the model')
    parser.add_argument('--num_points', type=int, default=1024,
                        help='num of points to use')
    parser.add_argument('--dropout', type=float, default=0.5,
                        help='dropout rate')
    parser.add_argument('--emb_dims', type=int, default=1024, metavar='N',
                        help='Dimension of embeddings')
    parser.add_argument('--input_pts', type=int, default=1024, metavar='N',
                        help='#')          
    parser.add_argument('--alpha1', type=float, default=0.1, metavar='N',
                        help='#')   
    parser.add_argument('--alpha2', type=float, default=0.1, metavar='N',
                        help='#')         
    parser.add_argument('--k', type=int, default=20, metavar='N',
                        help='Num of nearest neighbors to use')
    parser.add_argument('--model_path', type=str, default='', metavar='N',
                        help='Pretrained model path')
    parser.add_argument('--debug', type=bool, default=False,
                        help='Debug mode')
    parser.add_argument('--similarity_metric', type=str, default='exponential', metavar='N',
                        help='how to measure similarity: exponential or reciprocal')
    args = parser.parse_args()

    #_init_()

    #io = IOStream('checkpoints/' + args.exp_name + '/run.log')
    #io.cprint(str(args))

    args.cuda = not args.no_cuda and torch.cuda.is_available()
    torch.manual_seed(args.seed)

    if not args.eval:
        train(args)
    else:
        test(args)
