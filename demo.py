import torch
import torch.nn as nn
import torch.nn.init as init
import torch.nn.functional as F
from torch.utils.data import DataLoader
import math
import os
import sys
import numpy as np
import time

import torch.optim as optim
import re
import json
import tempfile
import shutil
import cv2
import face_alignment
import matplotlib.pyplot as plt
import uuid
from tqdm import tqdm, trange
from pathlib import Path
from joblib import Parallel, delayed
from time import perf_counter
import subprocess
import ffmpeg

def get_position(size, padding=0.25):
    
    x = [0.000213256, 0.0752622, 0.18113, 0.29077, 0.393397, 0.586856, 0.689483, 0.799124,
                    0.904991, 0.98004, 0.490127, 0.490127, 0.490127, 0.490127, 0.36688, 0.426036,
                    0.490127, 0.554217, 0.613373, 0.121737, 0.187122, 0.265825, 0.334606, 0.260918,
                    0.182743, 0.645647, 0.714428, 0.793132, 0.858516, 0.79751, 0.719335, 0.254149,
                    0.340985, 0.428858, 0.490127, 0.551395, 0.639268, 0.726104, 0.642159, 0.556721,
                    0.490127, 0.423532, 0.338094, 0.290379, 0.428096, 0.490127, 0.552157, 0.689874,
                    0.553364, 0.490127, 0.42689]
    
    y = [0.106454, 0.038915, 0.0187482, 0.0344891, 0.0773906, 0.0773906, 0.0344891,
                    0.0187482, 0.038915, 0.106454, 0.203352, 0.307009, 0.409805, 0.515625, 0.587326,
                    0.609345, 0.628106, 0.609345, 0.587326, 0.216423, 0.178758, 0.179852, 0.231733,
                    0.245099, 0.244077, 0.231733, 0.179852, 0.178758, 0.216423, 0.244077, 0.245099,
                    0.780233, 0.745405, 0.727388, 0.742578, 0.727388, 0.745405, 0.780233, 0.864805,
                    0.902192, 0.909281, 0.902192, 0.864805, 0.784792, 0.778746, 0.785343, 0.778746,
                    0.784792, 0.824182, 0.831803, 0.824182]
    
    x, y = np.array(x), np.array(y)
    
    x = (x + padding) / (2 * padding + 1)
    y = (y + padding) / (2 * padding + 1)
    x = x * size
    y = y * size
    return np.array(list(zip(x, y)))

def cal_area(anno):
    return (anno[:,0].max() - anno[:,0].min()) * (anno[:,1].max() - anno[:,1].min()) 

def output_video(p, txt, dst):
    files = os.listdir(p)
    files = sorted(files, key=lambda x: int(os.path.splitext(x)[0]))

    font = cv2.FONT_HERSHEY_SIMPLEX
    
    for file, line in zip(files, txt):
        img = cv2.imread(os.path.join(p, file))
        h, w, _ = img.shape
        img = cv2.putText(img, line, (w//8, 11*h//12), font, 1.2, (0, 0, 0), 3, cv2.LINE_AA)
        img = cv2.putText(img, line, (w//8, 11*h//12), font, 1.2, (255, 255, 255), 0, cv2.LINE_AA)  
        h = h // 2
        w = w // 2
        img = cv2.resize(img, (w, h))     
        cv2.imwrite(os.path.join(p, file), img)
    
    cmd = "ffmpeg -y -i {}/%d.jpg -r 25 \'{}\'".format(p, dst)
    os.system(cmd)

def transformation_from_points(points1, points2):
    points1 = points1.astype(np.float64)
    points2 = points2.astype(np.float64)
 
    c1 = np.mean(points1, axis=0)
    c2 = np.mean(points2, axis=0)
    points1 -= c1
    points2 -= c2
    s1 = np.std(points1)
    s2 = np.std(points2)
    points1 /= s1
    points2 /= s2
 
    U, S, Vt = np.linalg.svd(points1.T * points2)
    R = (U * Vt).T
    return np.vstack([np.hstack(((s2 / s1) * R,
                                       c2.T - (s2 / s1) * R * c1.T)),
                         np.matrix([0., 0., 1.])])

def load_video(file, gpu_idx):
    p = tempfile.mkdtemp()

    stream = ffmpeg.input(file, nostdin=None)
    stream = ffmpeg.output(stream, f'{p}/%d.jpg', **{'qscale:v': 2, 'r':25}, loglevel="quiet")
    
    try:
        out, err = ffmpeg.run(stream)
    except:
        print(f"ffmpeg couldn't decode file: {file}")
        return None, p

    if err:
        print(err)
        return None, p

    #cmd = 'ffmpeg -i \'{}\' -qscale:v 2 -r 25 \'{}/%d.jpg\''.format(file, p)
    
    files = os.listdir(p)
    files = sorted(files, key=lambda x: int(os.path.splitext(x)[0]))
        
    #array = [cv2.imread(os.path.join(p, file)) for file in files]
    
    
    #array = list(filter(lambda im: not im is None, array))
    #array = [cv2.resize(im, (100, 50), interpolation=cv2.INTER_LANCZOS4) for im in array]
    
    fa = face_alignment.FaceAlignment(face_alignment.LandmarksType._2D, flip_input=False, device=f'cuda:{gpu_idx}')
    points = fa.get_landmarks_from_directory(p, show_progress_bar=False)
    points = sorted(points.items(), key=lambda x: int(Path(x[0]).stem))

    #points = [fa.get_landmarks(I) for I in array]
    front256 = get_position(256)
    video = []
    for filename, point in points:
        if(point is not None):
            shape = np.array(point[0])
            shape = shape[17:]
            M = transformation_from_points(np.matrix(shape), np.matrix(front256))
           
            img = cv2.warpAffine(cv2.imread(filename), M[:2], (256, 256))
            (x, y) = front256[-20:].mean(0).astype(np.int32)
            w = 160//2
            img = img[y-w//2:y+w//2,x-w:x+w,...]
            img = cv2.resize(img, (128, 64))
            # plt.imshow(img)
            # plt.savefig(f"tmp_img/{uuid.uuid1()}.jpg")
            video.append(img)
        elif video:
            video.append(video[-1])
        else:
            print("first frame is missing", file)


    if not video:
        print("No faces in video")
        return None, p


    video = np.stack(video, axis=0).astype(np.float32)
    video = torch.FloatTensor(video.transpose(3, 0, 1, 2)) / 255.0
    del fa

    return video, p


def ctc_decode(y):
    y = y.argmax(-1)
    t = y.size(0)
    result = []
    for i in range(t+1):
        result.append(MyDataset.ctc_arr2txt(y[:i], start=1))
    return result

def process(gpu_idx, video_name):
    video, img_p = load_video(video_name, gpu_idx % 6)
    shutil.rmtree(img_p)
    return video
        

if(__name__ == '__main__'):
    from dataset import MyDataset
    from model import LipNet
    opt = __import__('options')
    #os.environ['CUDA_VISIBLE_DEVICES'] = opt.gpu    
    
    
    model = LipNet()
    model = model.cuda()
    model.eval()
    net = nn.DataParallel(model).cuda()

    if(hasattr(opt, 'weights')):
        pretrained_dict = torch.load(opt.weights)
        model_dict = model.state_dict()
        pretrained_dict = {k: v for k, v in pretrained_dict.items() if k in model_dict.keys() and v.size() == model_dict[k].size()}
        missed_params = [k for k, v in model_dict.items() if not k in pretrained_dict.keys()]
        print('loaded params/tot params:{}/{}'.format(len(pretrained_dict),len(model_dict)))
        print('miss matched params:{}'.format(missed_params))
        model_dict.update(pretrained_dict)
        model.load_state_dict(model_dict)
        

    
    all_files = list(Path('test').rglob('*.mp4'))
    files = []
    for file in tqdm(all_files):
        if not os.path.exists(f"video_features_test_tmp/{file.parts[-2]}_{file.stem}.npy"):
            files.append(file)

    print(f"Total number of files is {len(all_files)}")
    print(f"The number of unprocessed files is {len(files)}")
    batch_size = 60
    for i in trange(0, len(files), batch_size):
        videos = Parallel(n_jobs=1)(delayed(process)(idx, video_name) for idx, video_name in enumerate(files[i:i+batch_size]))


        start_time = perf_counter()
        for idx, video in enumerate(videos):
            video_name = files[i+idx]
            if video is None:
                print(video_name)
                continue
            y = model(video[None,...].cuda())
            #torch.save(y, f"video_features_test_tmp/{video_name.parts[-2]}_{video_name.stem}.npy")
        print(perf_counter() - start_time)
    #txt = ctc_decode(y[0])
    
    #output_video(img_p, txt, "out.mp4")
