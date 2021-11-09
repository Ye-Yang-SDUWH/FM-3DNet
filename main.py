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
    train_loader = DataLoader(ModelNet40(partition='train', num_points=args.num_points), num_workers=8,
                              batch_size=args.batch_size, shuffle=True, drop_last=True)
    test_loader = DataLoader(ModelNet40(partition='test', num_points=args.num_points), num_workers=8,
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

    best_test_acc = 0
    # ximin
    if 'start_epoch' in args:
        start_epoch = args.start_epoch + 1
    else:
        start_epoch = 0
    for epoch in range(start_epoch, args.epochs):
        scheduler.step()
        ####################
        # Train
        ####################
        #train_loss = 0.0
        count = 0.0
        model.train()
        final_loss_list = list()
        for pointcloud, transformed_point_cloud in train_loader:
            pointcloud = pointcloud.to(device)  #b*1024*3
            transformed_point_cloud = transformed_point_cloud.to(device)
            pointcloud = pointcloud.permute(0, 2, 1) #b*3*1024
            transformed_point_cloud = transformed_point_cloud.permute(0, 2, 1)
            batch_size = pointcloud.size()[0]
            opt.zero_grad()
            fe1_nograd, fe2_nograd, fe1_final, fe2_final, M = model(pointcloud, transformed_point_cloud)
            final_loss, FB_loss, M_loss1,M_loss2 = loss_function(fe1_nograd, fe2_nograd, fe1_final, fe2_final, M)
            final_loss.backward()
            final_loss_list.append(final_loss.data)
            opt.step()
            #preds = logits.max(dim=1)[1]
            count += batch_size

            # ximin
            print(f"training {count}")

        outstr = 'Train %d, loss: %.6f' % (epoch,
                                            final_loss_list[-1],
                                            )
        # ximin
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

        ####################
        # Test
        ####################
        '''
        test_loss = 0.0
        count = 0.0
        model.eval()
        #test_pred = []
        #test_true = []
        test_loss_list = list()
        for pointcloud, transformed_point_cloud in test_loader:
            pointcloud = pointcloud.to(device)  # b*1024*3
            transformed_point_cloud = transformed_point_cloud.to(device)
            pointcloud = pointcloud.permute(0, 2, 1)  # b*3*1024
            transformed_point_cloud = transformed_point_cloud.permute(0, 2, 1)
            batch_size = pointcloud.size()[0]
            fe1_nograd, fe2_nograd, fe1_final, fe2_final, p = model(pointcloud, transformed_point_cloud)
            final_loss, FB_loss, P_loss1, P_loss2 = loss_function(fe1_nograd, fe2_nograd, fe1_final, fe2_final, p)
            test_loss_list.append(final_loss.data())
            opt.step()
            # preds = logits.max(dim=1)[1]
            count += batch_size
            # train_loss += loss.item() * batch_size
            # train_true.append(label.cpu().numpy())
            # train_pred.append(preds.detach().cpu().numpy())
        # train_true = np.concatenate(train_true)
        # train_pred = np.concatenate(train_pred)
        outstr = 'Train %d, loss: %.6f' % (epoch,
                                           test_loss_list[-1],
                                           )
        '''

def test(args):  #not written
    test_loader = DataLoader(ModelNet40(partition='test', num_points=args.num_points),
                             batch_size=args.test_batch_size, shuffle=True, drop_last=False)

    device = torch.device("cuda" if args.cuda else "cpu")

    #Try to load models
    model = DGCNN(args).to(device)
    model = nn.DataParallel(model)
    # ximin
    checkpoint = torch.load(args.model_path)
    model.module.load_state_dict(checkpoint['DGCNN_state_dict'])
    # model.load_state_dict(torch.load(args.model_path))
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
    parser.add_argument('--exp_name', type=str, default='exp', metavar='N',
                        help='Name of the experiment')
    parser.add_argument('--model', type=str, default='dgcnn', metavar='N',
                        choices=['pointnet', 'dgcnn'],
                        help='Model to use, [pointnet, dgcnn]')
    parser.add_argument('--dataset', type=str, default='modelnet40', metavar='N',
                        choices=['modelnet40'])
    parser.add_argument('--batch_size', type=int, default=4, metavar='batch_size',
                        help='Size of batch)')
    parser.add_argument('--test_batch_size', type=int, default=4, metavar='batch_size',
                        help='Size of batch)')
    parser.add_argument('--epochs', type=int, default=10, metavar='N',
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
