import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable
from torch.optim.lr_scheduler import StepLR
import torchvision.models as models
import numpy as np
import os
import math
import argparse
import random
import cv2
import subprocess
import time
from tqdm import tqdm


torch.backends.cudnn.benchmark = True
parser = argparse.ArgumentParser(description="One Shot Visual Recognition")
parser.add_argument("-i","--input_dim",type = int, default = 224)
parser.add_argument("-f","--feature_dim",type = int, default = 64)
parser.add_argument("-r","--relation_dim",type = int, default = 8)
parser.add_argument("-w","--class_num",type = int, default = 1)
parser.add_argument("-s","--sample_num_per_class",type = int, default = 5)
parser.add_argument("-b","--batch_num_per_class",type = int, default = 1)
parser.add_argument("-e","--episode",type = int, default= 50000)
parser.add_argument("-o","--overlay_mask", type = int, default = 1)
parser.add_argument("-l","--learning_rate", type = float, default = 0.001)
parser.add_argument("-g","--gpu",type=int, default=0)
parser.add_argument("-u","--hidden_unit",type=int,default=10)
parser.add_argument("-d","--display_query_num",type=int,default=5)
parser.add_argument("-t","--test_class",type=int,default=1)
parser.add_argument("-modelf","--feature_encoder_model",type=str,default='models/feature_encoder.pkl')
parser.add_argument("-modelr","--relation_network_model",type=str,default='models/relation_network.pkl')
parser.add_argument("-sd","--support_dir",type=str,default='data/african_elephant/supp')
parser.add_argument("-td","--test_dir",type=str,default='data/african_elephant/test')
args = parser.parse_args()

os.environ["CUDA_VISIBLE_DEVICES"]=str(np.argmax( [int(x.split()[2]) \
                                    for x in subprocess.Popen("nvidia-smi -q -d Memory |\
                                    grep -A4 GPU | grep Free", shell=True, stdout=subprocess.PIPE).stdout.readlines()] ))

# Hyper Parameters
FEATURE_DIM = args.feature_dim
RELATION_DIM = args.relation_dim
CLASS_NUM = args.class_num
SAMPLE_NUM_PER_CLASS = args.sample_num_per_class
BATCH_NUM_PER_CLASS = args.batch_num_per_class
EPISODE = args.episode
# TEST_EPISODE = args.test_episode
LEARNING_RATE = args.learning_rate
GPU = args.gpu
HIDDEN_UNIT = args.hidden_unit
DISPLAY_QUERY = args.display_query_num
TEST_CLASS = args.test_class
FEATURE_MODEL = args.feature_encoder_model
RELATION_MODEL = args.relation_network_model

input_dim = args.input_dim
overlay_mask = args.overlay_mask
assert (input_dim%224==0)

class CNNEncoder(nn.Module):
    """docstring for ClassName"""
    def __init__(self):
        super(CNNEncoder, self).__init__()
        features = list(models.vgg16_bn(pretrained=False).features)
        self.layer1 = nn.Sequential(
                        nn.Conv2d(4,64,kernel_size=3,padding=1)
                        )
        self.features = nn.ModuleList(features)[1:]#.eval()
        # print (nn.Sequential(*list(models.vgg16_bn(pretrained=True).children())[0]))
        # self.features = nn.ModuleList(features).eval()

    def forward(self,x):
        results = []
        x = self.layer1(x)
        for ii, model in enumerate(self.features):
            x = model(x)
            if ii in {4, 11, 21, 31, 41}:
                results.append(x)

        return x, results

class RelationNetwork(nn.Module):
    """docstring for RelationNetwork"""
    def __init__(self):
        super(RelationNetwork, self).__init__()
        self.layer1 = nn.Sequential(
                        nn.Conv2d(1024,512,kernel_size=3,padding=1),
                        nn.BatchNorm2d(512, momentum=1, affine=True),
                        nn.ReLU()
                        )
        self.layer2 = nn.Sequential(
                        nn.Conv2d(512,512,kernel_size=3,padding=1),
                        nn.BatchNorm2d(512, momentum=1, affine=True),
                        nn.ReLU()
                        )
        self.upsample = nn.Upsample(scale_factor=2, mode='bilinear', align_corners=True)
        self.double_conv1 = nn.Sequential(
                        nn.Conv2d(1024,512,kernel_size=3,padding=1),
                        nn.BatchNorm2d(512, momentum=1, affine=True),
                        nn.ReLU(),
                        nn.Conv2d(512,512,kernel_size=3,padding=1),
                        nn.BatchNorm2d(512, momentum=1, affine=True),
                        nn.ReLU()
                        ) # 14 x 14
        self.double_conv2 = nn.Sequential(
                        nn.Conv2d(1024,256,kernel_size=3,padding=1),
                        nn.BatchNorm2d(256, momentum=1, affine=True),
                        nn.ReLU(),
                        nn.Conv2d(256,256,kernel_size=3,padding=1),
                        nn.BatchNorm2d(256, momentum=1, affine=True),
                        nn.ReLU()
                        ) # 28 x 28
        self.double_conv3 = nn.Sequential(
                        nn.Conv2d(512,128,kernel_size=3,padding=1),
                        nn.BatchNorm2d(128, momentum=1, affine=True),
                        nn.ReLU(),
                        nn.Conv2d(128,128,kernel_size=3,padding=1),
                        nn.BatchNorm2d(128, momentum=1, affine=True),
                        nn.ReLU()
                        ) # 56 x 56
        self.double_conv4 = nn.Sequential(
                        nn.Conv2d(256,64,kernel_size=3,padding=1),
                        nn.BatchNorm2d(64, momentum=1, affine=True),
                        nn.ReLU(),
                        nn.Conv2d(64,64,kernel_size=3,padding=1),
                        nn.BatchNorm2d(64, momentum=1, affine=True),
                        nn.ReLU()
                        ) # 112 x 112
        self.double_conv5 = nn.Sequential(
                        nn.Conv2d(128,64,kernel_size=3,padding=1),
                        nn.BatchNorm2d(64, momentum=1, affine=True),
                        nn.ReLU(),
                        nn.Conv2d(64,1,kernel_size=1,padding=0),
                        ) # 256 x 256


    def forward(self,x,concat_features):
        out = self.layer1(x)
        out = self.layer2(out)
        out = self.upsample(out) #block 1
        out = torch.cat((out, concat_features[-1]), dim=1)
        out = self.double_conv1(out)
        out = self.upsample(out) #block 2
        out = torch.cat((out, concat_features[-2]), dim=1)
        out = self.double_conv2(out)
        out = self.upsample(out) #block 3
        out = torch.cat((out, concat_features[-3]), dim=1)
        out = self.double_conv3(out)
        out = self.upsample(out) #block 4
        out = torch.cat((out, concat_features[-4]), dim=1)
        out = self.double_conv4(out)
        out = self.upsample(out) #block 5
        out = torch.cat((out, concat_features[-5]), dim=1)
        out = self.double_conv5(out)

        out = F.sigmoid(out)
        return out

def get_oneshot_batch(testname):  #shuffle in query_images not done
    support_images = np.zeros((CLASS_NUM*SAMPLE_NUM_PER_CLASS,3,input_dim,input_dim), dtype=np.float32)
    support_labels = np.zeros((CLASS_NUM*SAMPLE_NUM_PER_CLASS,CLASS_NUM,input_dim,input_dim), dtype=np.float32)
    query_images = np.zeros((CLASS_NUM*BATCH_NUM_PER_CLASS,3,input_dim,input_dim), dtype=np.float32)
    query_labels = np.zeros((CLASS_NUM*BATCH_NUM_PER_CLASS,CLASS_NUM,input_dim,input_dim), dtype=np.float32)
    zeros = np.zeros((CLASS_NUM*BATCH_NUM_PER_CLASS,1,input_dim,input_dim), dtype=np.float32)
    class_cnt = 0
    imgnames = os.listdir('./%s/label' % args.support_dir)
    testnames = os.listdir('%s' % args.test_dir)
    indexs = list(range(0,len(imgnames)))[0:5]
    chosen_index = indexs
    j = 0
    for k in chosen_index:
        # process image
        image = cv2.imread('%s/image/%s' % (args.support_dir, imgnames[k]))
        if image is None:
            print('%s/image/%s' % (args.support_dir, imgnames[k]))
            raise Exception('cannot load image ')
        if not image.shape[0] == input_dim:
          image = cv2.resize(image, (input_dim, input_dim))
        image = image[:,:,::-1] # bgr to rgb
        image = image / 255.0
        image = np.transpose(image, (2,0,1))
        label = cv2.imread('%s/label/%s' % (args.support_dir, imgnames[k]))[:,:,0]
        label = cv2.resize(label, (input_dim, input_dim), interpolation=cv2.INTER_NEAREST)

        support_images[k] = image
        support_labels[k][0] = label

    testimage = cv2.imread('%s/%s' % (args.test_dir, testname))
    testimage = cv2.resize(testimage, (input_dim,input_dim))
    testimage = testimage[:,:,::-1] # bgr to rgb
    testimage = testimage / 255.0
    testimage = np.transpose(testimage, (2,0,1))

    query_images[0] = testimage

    class_cnt += 1
    support_images_tensor = torch.from_numpy(support_images)
    support_labels_tensor = torch.from_numpy(support_labels)
    support_images_tensor = torch.cat((support_images_tensor,support_labels_tensor), dim=1)

    zeros_tensor = torch.from_numpy(zeros)
    query_images_tensor = torch.from_numpy(query_images)
    query_images_tensor = torch.cat((query_images_tensor,zeros_tensor), dim=1)
    query_labels_tensor = torch.from_numpy(query_labels)

    return support_images_tensor, support_labels_tensor, query_images_tensor, query_labels_tensor

  
def maskimg(img, mask, edge, color=[0, 0, 255], alpha=0.5):
    '''
    img: cv2 image
    mask: bool or np.where
    color: BGR triplet [_, _, _]. Default: [0, 255, 255] is yellow.
    alpha: float [0, 1].
    Ref: http://www.pyimagesearch.com/2016/03/07/transparent-overlays-with-opencv/
    '''
    out = img.copy()
    img_layer = img.copy()
    img_layer[mask==255] = color
    edge_layer = img.copy()
    edge_layer[edge==255] = color
    out = cv2.addWeighted(edge_layer, 1, out, 0 , 0, out)
    out = cv2.addWeighted(img_layer, alpha, out, 1 - alpha, 0, out)
    return(out)



def main():
    # Step 1: init data folders
    print("init data folders")
    # init character folders for dataset construction
    # metatrain_character_folders,metatest_character_folders = tg.omniglot_character_folders()

    # Step 2: init neural networks
    print("init neural networks")

    feature_encoder = CNNEncoder()
    relation_network = RelationNetwork()

    feature_encoder.cuda(GPU)
    relation_network.cuda(GPU)

    if os.path.exists(FEATURE_MODEL):
        feature_encoder.load_state_dict(torch.load(FEATURE_MODEL))
        print("load feature encoder success")
    else:
        raise Exception('Can not load feature encoder: %s' % FEATURE_MODEL)
    if os.path.exists(RELATION_MODEL):
        relation_network.load_state_dict(torch.load(RELATION_MODEL))
        print("load relation network success")
    else:
        raise Exception('Can not load relation network: %s' % RELATION_MODEL)


    print("Testing...")
    meaniou = 0
    classname = args.support_dir
    if os.path.exists('result1'):
        os.system('rm -r result1')
    if os.path.exists('result.zip'):
        os.system('rm result.zip')
    if not os.path.exists('result1'):
        os.makedirs('result1')
    if not os.path.exists('./result1/%s' % classname):
        os.makedirs('./result1/%s' % classname)
    stick = np.zeros((input_dim*4,input_dim*5,3), dtype=np.uint8)
    support_image = np.zeros((5, 3, input_dim, input_dim), dtype=np.float32)
    support_label = np.zeros((5, 1, input_dim, input_dim), dtype=np.float32)
    supp_demo = np.zeros((input_dim, input_dim*5,3), dtype=np.uint8)
    supplabel_demo = np.zeros((input_dim, input_dim*5,3), dtype=np.uint8)

    testnames = os.listdir('%s' % args.test_dir)
    print ('%s testing images in class %s' % (len(testnames), classname))

    for cnt, testname in tqdm(enumerate(testnames), total=len(testnames)):
        if cv2.imread('%s/%s' % (args.test_dir, testname)) is None:
            continue


        samples, sample_labels, batches, batch_labels = get_oneshot_batch(testname)

        #forward
        with torch.no_grad():
          sample_features, _ = feature_encoder(Variable(samples).cuda(GPU))
          sample_features = sample_features.view(CLASS_NUM,SAMPLE_NUM_PER_CLASS,512,input_dim//32,input_dim//32)
          sample_features = torch.sum(sample_features,1).squeeze(1) # 1*512*7*7
          batch_features, ft_list = feature_encoder(Variable(batches).cuda(GPU))
          sample_features_ext = sample_features.unsqueeze(0).repeat(BATCH_NUM_PER_CLASS*CLASS_NUM,1,1,1,1)
          batch_features_ext = batch_features.unsqueeze(0).repeat(CLASS_NUM,1,1,1,1)
          batch_features_ext = torch.transpose(batch_features_ext,0,1)
          relation_pairs = torch.cat((sample_features_ext,batch_features_ext),2).view(-1,1024,input_dim//32,input_dim//32)
          output = relation_network(relation_pairs,ft_list).view(-1,CLASS_NUM,input_dim,input_dim)

        classiou = 0
        for i in range(0, batches.size()[0]):
            #get prediction
            pred = output.data.cpu().numpy()[i][0]
            pred[pred<=0.5] = 0
            pred[pred>0.5] = 1
            #vis
            demo = cv2.cvtColor(pred, cv2.COLOR_GRAY2RGB) * 255
            stick[input_dim*3:input_dim*4, input_dim*i:input_dim*(i+1),:] = demo.copy()

            testlabel = batch_labels.numpy()[i][0].astype(bool)
            pred = pred.astype(bool)
            #compute IOU
            overlap = testlabel * pred
            union = testlabel + pred
            iou = overlap.sum() / float(union.sum())
            # print ('iou=%0.4f' % iou)
            classiou += iou
        classiou /= 5.0

        #visulization
        if (cnt == 0):
            for i in range(0, samples.size()[0]):
                suppimg = np.transpose(samples.numpy()[i][0:3], (1,2,0))[:,:,::-1] * 255
                supplabel = np.transpose(sample_labels.numpy()[i], (1,2,0))
                supplabel = cv2.cvtColor(supplabel, cv2.COLOR_GRAY2RGB)
                supplabel = (supplabel * 255).astype(np.uint8)
                suppedge = cv2.Canny(supplabel,1,1)

        testimg = np.transpose(batches.numpy()[0][0:3], (1,2,0))[:,:,::-1] * 255
        testlabel = stick[input_dim*3:input_dim*4, input_dim*i:input_dim*(i+1),:].astype(np.uint8)
        testedge = cv2.Canny(testlabel,1,1)
        if overlay_mask:
          cv2.imwrite('./result1/%s/%s' % (classname,testname), maskimg(testimg, testlabel.copy()[:,:,0], testedge))
        else:
          cv2.imwrite('./result1/%s/%s' % (classname,testname), testlabel)

if __name__ == '__main__':
    main()
