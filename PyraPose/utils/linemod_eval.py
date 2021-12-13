"""
Copyright 2017-2018 Fizyr (https://fizyr.com)

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

#from pycocotools.cocoeval import COCOeval

import os
import numpy as np
import transforms3d as tf3d
import copy
import cv2
import open3d
from ..utils import ply_loader
from ..utils.anchors import locations_for_shape
from .pose_error import reproj, add, adi, re, te, vsd
import yaml
import sys
import matplotlib.pyplot as plt
import time
from dual_quaternions import DualQuaternion

import progressbar
assert(callable(progressbar.progressbar)), "Using wrong progressbar module, install 'progressbar2' instead."


# LineMOD
fxkin = 572.41140
fykin = 573.57043
cxkin = 325.26110
cykin = 242.04899


def get_evaluation_kiru(pcd_temp_,pcd_scene_,inlier_thres,tf,final_th, model_dia):#queue
    tf_pcd =np.eye(4)
    pcd_temp_.transform(tf)

    mean_temp = np.mean(np.array(pcd_temp_.points)[:, 2])
    mean_scene = np.median(np.array(pcd_scene_.points)[:, 2])
    pcd_diff = mean_scene - mean_temp

    #open3d.draw_geometries([pcd_temp_])
    # align model with median depth of scene
    new_pcd_trans = []
    for i, point in enumerate(pcd_temp_.points):
        poi = np.asarray(point)
        poi = poi + [0.0, 0.0, pcd_diff]
        new_pcd_trans.append(poi)
    tf = np.array(tf)
    tf[2, 3] = tf[2, 3] + pcd_diff
    pcd_temp_.points = open3d.Vector3dVector(np.asarray(new_pcd_trans))
    open3d.estimate_normals(pcd_temp_, search_param=open3d.KDTreeSearchParamHybrid(
        radius=5.0, max_nn=10))

    pcd_min = mean_scene - (model_dia * 2)
    pcd_max = mean_scene + (model_dia * 2)
    new_pcd_scene = []
    for i, point in enumerate(pcd_scene_.points):
        if point[2] > pcd_min or point[2] < pcd_max:
            new_pcd_scene.append(point)
    pcd_scene_.points = open3d.Vector3dVector(np.asarray(new_pcd_scene))
    #open3d.draw_geometries([pcd_scene_])
    open3d.estimate_normals(pcd_scene_, search_param=open3d.KDTreeSearchParamHybrid(
        radius=5.0, max_nn=10))

    reg_p2p = open3d.registration.registration_icp(pcd_temp_,pcd_scene_ , inlier_thres, np.eye(4),
                                                   open3d.registration.TransformationEstimationPointToPoint(),
                                                   open3d.registration.ICPConvergenceCriteria(max_iteration = 5)) #5?
    tf = np.matmul(reg_p2p.transformation,tf)
    tf_pcd = np.matmul(reg_p2p.transformation,tf_pcd)
    pcd_temp_.transform(reg_p2p.transformation)

    open3d.estimate_normals(pcd_temp_, search_param=open3d.KDTreeSearchParamHybrid(
        radius=2.0, max_nn=30))
    #open3d.draw_geometries([pcd_scene_])
    points_unfiltered = np.asarray(pcd_temp_.points)
    last_pcd_temp = []
    for i, normal in enumerate(pcd_temp_.normals):
        if normal[2] < 0:
            last_pcd_temp.append(points_unfiltered[i, :])
    if not last_pcd_temp:
        normal_array = np.asarray(pcd_temp_.normals) * -1
        pcd_temp_.normals = open3d.Vector3dVector(normal_array)
        points_unfiltered = np.asarray(pcd_temp_.points)
        last_pcd_temp = []
        for i, normal in enumerate(pcd_temp_.normals):
            if normal[2] < 0:
                last_pcd_temp.append(points_unfiltered[i, :])
    #print(np.asarray(last_pcd_temp))
    pcd_temp_.points = open3d.Vector3dVector(np.asarray(last_pcd_temp))

    open3d.estimate_normals(pcd_temp_, search_param=open3d.KDTreeSearchParamHybrid(
        radius=5.0, max_nn=30))

    hyper_tresh = inlier_thres
    for i in range(4):
        inlier_thres = reg_p2p.inlier_rmse*2
        hyper_thres = hyper_tresh * 0.75
        if inlier_thres < 1.0:
            inlier_thres = hyper_tresh * 0.75
            hyper_tresh = inlier_thres
        reg_p2p = open3d.registration.registration_icp(pcd_temp_,pcd_scene_ , inlier_thres, np.eye(4),
                                                       open3d.registration.TransformationEstimationPointToPlane(),
                                                       open3d.registration.ICPConvergenceCriteria(max_iteration = 1)) #5?
        tf = np.matmul(reg_p2p.transformation,tf)
        tf_pcd = np.matmul(reg_p2p.transformation,tf_pcd)
        pcd_temp_.transform(reg_p2p.transformation)
    inlier_rmse = reg_p2p.inlier_rmse

    #open3d.draw_geometries([pcd_temp_, pcd_scene_])

    ##Calculate fitness with depth_inlier_th
    if(final_th>0):

        inlier_thres = final_th #depth_inlier_th*2 #reg_p2p.inlier_rmse*3
        reg_p2p = open3d.registration.registration_icp(pcd_temp_,pcd_scene_, inlier_thres, np.eye(4),
                                                       open3d.registration.TransformationEstimationPointToPlane(),
                                                       open3d.registration.ICPConvergenceCriteria(max_iteration = 1)) #5?
        tf = np.matmul(reg_p2p.transformation, tf)
        tf_pcd = np.matmul(reg_p2p.transformation, tf_pcd)
        pcd_temp_.transform(reg_p2p.transformation)

    #open3d.draw_geometries([last_pcd_temp_, pcd_scene_])

    if( np.abs(np.linalg.det(tf[:3,:3])-1)>0.001):
        tf[:3,0]=tf[:3,0]/np.linalg.norm(tf[:3,0])
        tf[:3,1]=tf[:3,1]/np.linalg.norm(tf[:3,1])
        tf[:3,2]=tf[:3,2]/np.linalg.norm(tf[:3,2])
    if( np.linalg.det(tf) < 0) :
        tf[:3,2]=-tf[:3,2]

    return tf,inlier_rmse,tf_pcd,reg_p2p.fitness


def toPix_array(translation):

    xpix = ((translation[:, 0] * fxkin) / translation[:, 2]) + cxkin
    ypix = ((translation[:, 1] * fykin) / translation[:, 2]) + cykin

    return np.stack((xpix, ypix), axis=1) #, zpix]


def load_pcd(data_path, cat):
    # load meshes
    ply_path = os.path.join(data_path, 'meshes', 'obj_' + cat + '.ply')
    pcd_model = open3d.io.read_point_cloud(ply_path)
    model_vsd = {}
    model_vsd['pts'] = np.asarray(pcd_model.points)
    #open3d.estimate_normals(pcd_model, search_param=open3d.KDTreeSearchParamHybrid(
    #    radius=0.1, max_nn=30))
    # open3d.draw_geometries([pcd_model])
    model_vsd['pts'] = model_vsd['pts'] * 0.001

    return pcd_model, model_vsd
'''

def load_pcd(data_path, cat):
    # load meshes
    ply_path = os.path.join(data_path, 'meshes', 'obj_' + cat + '.ply')
    model_vsd = ply_loader.load_ply(ply_path)
    pcd_model = open3d.geometry.PointCloud()
    pcd_model.points = open3d.utility.Vector3dVector(model_vsd['pts'])
    pcd_model.estimate_normals(search_param=open3d.geometry.KDTreeSearchParamHybrid(
        radius=0.1, max_nn=30))
    # open3d.draw_geometries([pcd_model])
    model_vsd_mm = copy.deepcopy(model_vsd)
    model_vsd_mm['pts'] = model_vsd_mm['pts'] * 1000.0
    #pcd_model = open3d.read_point_cloud(ply_path)
    #pcd_model = None

    return pcd_model, model_vsd, model_vsd_mm
'''


def create_point_cloud(depth, fx, fy, cx, cy, ds):

    rows, cols = depth.shape

    depRe = depth.reshape(rows * cols)
    zP = np.multiply(depRe, ds)

    x, y = np.meshgrid(np.arange(0, cols, 1), np.arange(0, rows, 1), indexing='xy')
    yP = y.reshape(rows * cols) - cy
    xP = x.reshape(rows * cols) - cx
    yP = np.multiply(yP, zP)
    xP = np.multiply(xP, zP)
    yP = np.divide(yP, fy)
    xP = np.divide(xP, fx)

    cloud_final = np.transpose(np.array((xP, yP, zP)))
    #cloud_final[cloud_final[:,2]==0] = np.NaN

    return cloud_final


def boxoverlap(a, b):
    a = np.array([a[0], a[1], a[0] + a[2], a[1] + a[3]])
    b = np.array([b[0], b[1], b[0] + b[2], b[1] + b[3]])

    x1 = np.amax(np.array([a[0], b[0]]))
    y1 = np.amax(np.array([a[1], b[1]]))
    x2 = np.amin(np.array([a[2], b[2]]))
    y2 = np.amin(np.array([a[3], b[3]]))

    wid = x2-x1+1
    hei = y2-y1+1
    inter = wid * hei
    aarea = (a[2] - a[0] + 1) * (a[3] - a[1] + 1)
    barea = (b[2] - b[0] + 1) * (b[3] - b[1] + 1)
    # intersection over union overlap
    ovlap = inter / (aarea + barea - inter)
    # set invalid entries to 0 overlap
    maskwid = wid <= 0
    maskhei = hei <= 0
    np.where(ovlap, maskwid, 0)
    np.where(ovlap, maskhei, 0)

    return ovlap


def denorm_box(locations, regression, obj_diameter):
    mean = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    #std = [150, 150,  150,  150,  150,  150,  150,  150,  150,  150,  150, 150, 150, 150, 150, 150]
    std = np.full(16, 0.65)
    #std = np.full(18, 0.95)

    #regression = np.where(regression > 0, np.log(regression + 1.0), regression)
    #regression = np.where(regression < 0, -np.log(-regression + 1.0), regression)

    obj_diameter = obj_diameter * 1000.0

    x1 = locations[:, :, 0] - (regression[:, :, 0] * (std[0] * obj_diameter) + mean[0])
    y1 = locations[:, :, 1] - (regression[:, :, 1] * (std[1] * obj_diameter) + mean[1])
    x2 = locations[:, :, 0] - (regression[:, :, 2] * (std[2] * obj_diameter) + mean[2])
    y2 = locations[:, :, 1] - (regression[:, :, 3] * (std[3] * obj_diameter) + mean[3])
    x3 = locations[:, :, 0] - (regression[:, :, 4] * (std[4] * obj_diameter) + mean[4])
    y3 = locations[:, :, 1] - (regression[:, :, 5] * (std[5] * obj_diameter) + mean[5])
    x4 = locations[:, :, 0] - (regression[:, :, 6] * (std[6] * obj_diameter) + mean[6])
    y4 = locations[:, :, 1] - (regression[:, :, 7] * (std[7] * obj_diameter) + mean[7])
    x5 = locations[:, :, 0] - (regression[:, :, 8] * (std[8] * obj_diameter) + mean[8])
    y5 = locations[:, :, 1] - (regression[:, :, 9] * (std[9] * obj_diameter) + mean[9])
    x6 = locations[:, :, 0] - (regression[:, :, 10] * (std[10] * obj_diameter) + mean[10])
    y6 = locations[:, :, 1] - (regression[:, :, 11] * (std[11] * obj_diameter) + mean[11])
    x7 = locations[:, :, 0] - (regression[:, :, 12] * (std[12] * obj_diameter) + mean[12])
    y7 = locations[:, :, 1] - (regression[:, :, 13] * (std[13] * obj_diameter) + mean[13])
    x8 = locations[:, :, 0] - (regression[:, :, 14] * (std[14] * obj_diameter) + mean[14])
    y8 = locations[:, :, 1] - (regression[:, :, 15] * (std[15] * obj_diameter) + mean[15])
    #x9 = locations[:, :, 0] - (regression[:, :, 16] * (std[16] * obj_diameter) + mean[0])
    #y9 = locations[:, :, 1] - (regression[:, :, 17] * (std[17] * obj_diameter) + mean[1])

    pred_boxes = np.stack([x1, y1, x2, y2, x3, y3, x4, y4, x5, y5, x6, y6, x7, y7, x8, y8], axis=2)
    #pred_boxes = np.stack([x1, y1, x2, y2, x3, y3, x4, y4, x5, y5, x6, y6, x7, y7, x8, y8, x9, y9], axis=2)

    return pred_boxes


def evaluate_linemod(generator, model, data_path, threshold=0.3):

    mesh_info = os.path.join(data_path, "meshes/models_info.yml")
    threeD_boxes = np.ndarray((31, 8, 3), dtype=np.float32)
    model_dia = np.zeros((31), dtype=np.float32)
    avg_dimension = np.ndarray((16), dtype=np.float32)

    for key, value in yaml.load(open(mesh_info)).items():
        fac = 0.001
        x_minus = value['min_x'] * fac
        y_minus = value['min_y'] * fac
        z_minus = value['min_z'] * fac
        x_plus = value['size_x'] * fac + x_minus
        y_plus = value['size_y'] * fac + y_minus
        z_plus = value['size_z'] * fac + z_minus
        three_box_solo = np.array([
                                    #[0.0, 0.0, 0.0],
                                    [x_plus, y_plus, z_plus],
                                  [x_plus, y_plus, z_minus],
                                  [x_plus, y_minus, z_minus],
                                  [x_plus, y_minus, z_plus],
                                  [x_minus, y_plus, z_plus],
                                  [x_minus, y_plus, z_minus],
                                  [x_minus, y_minus, z_minus],
                                  [x_minus, y_minus, z_plus]])
        threeD_boxes[int(key), :, :] = three_box_solo
        model_dia[int(key)] = value['diameter'] * fac
        avg_dimension[int(key)] = ((value['size_x'] + value['size_y'] + value['size_z'])/3) * fac

    pc1, mv1 = load_pcd(data_path,'000001')
    pc2, mv2 = load_pcd(data_path,'000002')
    pc4, mv4 = load_pcd(data_path,'000004')
    pc5, mv5 = load_pcd(data_path,'000005')
    pc6, mv6 = load_pcd(data_path,'000006')
    pc8, mv8 = load_pcd(data_path,'000008')
    pc9, mv9 = load_pcd(data_path,'000009')
    pc10, mv10 = load_pcd(data_path,'000010')
    pc11, mv11 = load_pcd(data_path,'000011')
    pc12, mv12 = load_pcd(data_path,'000012')
    pc13, mv13 = load_pcd(data_path,'000013')
    pc14, mv14 = load_pcd(data_path,'000014')
    pc15, mv15 = load_pcd(data_path,'000015')

    allPoses = np.zeros((16), dtype=np.uint32)
    truePoses = np.zeros((16), dtype=np.uint32)
    falsePoses = np.zeros((16), dtype=np.uint32)
    trueDets = np.zeros((16), dtype=np.uint32)
    e_x = []
    e_y = []
    e_z = []
    e_roll = []
    e_pitch = []
    e_yaw = []

    for index in progressbar.progressbar(range(generator.size()), prefix='LineMOD evaluation: '):
        image_raw = generator.load_image(index)
        image = generator.preprocess_image(image_raw)
        image, scale = generator.resize_image(image)

        image_viz = copy.deepcopy(image_raw)

        anno = generator.load_annotations(index)

        if len(anno['labels']) < 1:
            continue

        if anno['labels'] == 6 or anno['labels'] == 2:
            continue

        checkLab = anno['labels']  # +1 to real_class
        for lab in checkLab:
            allPoses[int(lab) + 1] += 1

        cls = int(checkLab[0])
        true_cls = cls + 1

        # run network
        t_start = time.time()

        #boxes3D, scores, obj_residuals, centers = model.predict_on_batch(np.expand_dims(image, axis=0))#, np.expand_dims(image_dep, axis=0)])
        boxes3D, scores, labels, poses = model.predict_on_batch(np.expand_dims(image, axis=0))
        #boxes3D, scores = model.predict_on_batch(np.expand_dims(image, axis=0))
        #print('forward pass: ', time.time() - t_start)
        print('poses: ', poses.shape)

        boxes3D = boxes3D[labels == cls]
        scores = scores[labels == cls]
        poses = poses[labels == cls]
        #confs = confs[labels == cls]
        labels = labels[labels == cls]

        if len(labels) < 1:
            continue
        else:
            trueDets[true_cls] += 1

        '''
        n_hyps = 5
        if confs.shape[0] < 5:
            n_hyps = confs.shape[0]
        conf_ranks = np.argsort(confs[:, cls])
        confs_ranked = confs[conf_ranks, cls]
        poses_ranked = poses[conf_ranks, cls, :]
        print('conf_ranked: ', confs_ranked)
        '''

        poses_cls = poses[np.argmax(scores), cls, :]
        #poses_cls = np.mean(poses[:, cls, :], axis=0)
        #poses_cls = np.median(poses[:, cls, :], axis=0)
        pose_set = poses[:, cls, :]
        #poses_cls = poses[np.argmax(confs[:, cls]), cls, :]
        #dq = DualQuaternion.from_dq_array(poses_cls)
        #poses_cls = dq.homogeneous_matrix()
        #print(poses_cls)

        anno_ind = np.argwhere(anno['labels'] == checkLab)
        t_tra = anno['poses'][anno_ind[0][0]][:3]
        t_rot = anno['poses'][anno_ind[0][0]][3:]
        # print(t_rot)

        #BOP_obj_id = np.asarray([true_cat], dtype=np.uint32)

        # print(cls)

        if true_cls == 1:
            model_vsd = mv1
        elif true_cls == 2:
            model_vsd = mv2
        elif true_cls == 4:
            model_vsd = mv4
        elif true_cls == 5:
            model_vsd = mv5
        elif true_cls == 6:
            model_vsd = mv6
        elif true_cls == 8:
            model_vsd = mv8
        elif true_cls == 9:
            model_vsd = mv9
        elif true_cls == 10:
            model_vsd = mv10
        elif true_cls == 11:
            model_vsd = mv11
        elif true_cls == 12:
            model_vsd = mv12
        elif true_cls == 13:
            model_vsd = mv13
        elif true_cls == 14:
            model_vsd = mv14
        elif true_cls == 15:
            model_vsd = mv15

        ori_points = np.ascontiguousarray(threeD_boxes[true_cls, :, :], dtype=np.float32)  # .reshape((8, 1, 3))
        K = np.float32([fxkin, 0., cxkin, 0., fykin, cykin, 0., 0., 1.]).reshape(3, 3)
        t_rot = tf3d.quaternions.quat2mat(t_rot)
        R_gt = np.array(t_rot, dtype=np.float32).reshape(3, 3)
        t_gt = np.array(t_tra, dtype=np.float32)
        t_gt = t_gt * 0.001


        pose_votes = boxes3D
        k_hyp = boxes3D.shape[0]
        # min residual
        # res_idx = np.argmin(res_sum)
        # k_hyp = 1
        # pose_votes = pose_votes[:, res_idx, :]
        # max center
        # centerns = centers[0, cls_indices, 0]
        # centerns = np.squeeze(centerns)
        # max_center = np.argmax(centerns)
        # pose_votes = pose_votes[:, max_center, :]

        est_points = np.ascontiguousarray(pose_votes, dtype=np.float32).reshape((int(k_hyp * 8), 1, 2))
        obj_points = np.repeat(ori_points[np.newaxis, :, :], k_hyp, axis=0)
        obj_points = obj_points.reshape((int(k_hyp * 8), 1, 3))

        '''
        retval, orvec, otvec, inliers = cv2.solvePnPRansac(objectPoints=obj_points,
                                                           imagePoints=est_points, cameraMatrix=K,
                                                           distCoeffs=None, rvec=None, tvec=None,
                                                           useExtrinsicGuess=False, iterationsCount=300,
                                                           reprojectionError=5.0, confidence=0.99,
                                                           flags=cv2.SOLVEPNP_ITERATIVE)
        R_est, _ = cv2.Rodrigues(orvec)
        t_est = otvec.T
        t_est = t_est[0]
        '''

        # quaternion
        R_est = tf3d.quaternions.quat2mat(poses_cls[3:])
        t_est = poses_cls[:3] * 0.001
        # dual_quaternion
        #R_est = poses_cls[:3, :3]
        #t_est = poses_cls[:3, 3] * 0.001
        #t_est = t_est * -1.0
        # R6d
        #R_est = np.eye(3)
        #R_est[:3, 0] = np.linalg.norm(poses_cls[3:6])
        #R_est[:3, 1] = np.linalg.norm(poses_cls[6:])
        #R_est[:3, 2] = np.linalg.norm(R_est[:3, 0], np.cross(poses_cls[6:]))
        #t_est = poses_cls[:3] * 0.001

        R_best = R_est
        t_best = t_est

        e_x.append(abs(t_est[0] - t_gt[0]))
        e_y.append(abs(t_est[1] - t_gt[1]))
        e_z.append(abs(t_est[2] - t_gt[2]))
        euler_est = tf3d.euler.mat2euler(R_est)
        euler_gt = tf3d.euler.mat2euler(R_gt)
        e_roll.append(abs(euler_est[0] - euler_gt[0]))
        e_pitch.append(abs(euler_est[1] - euler_gt[1]))
        e_yaw.append(abs(euler_est[2] - euler_gt[2]))

        if cls == 10 or cls == 11:
            err_add = adi(R_est, t_est, R_gt, t_gt, model_vsd["pts"])
        else:
            err_add = add(R_est, t_est, R_gt, t_gt, model_vsd["pts"])
        if err_add < model_dia[true_cls] * 0.1:
            truePoses[true_cls] += 1
        print(' ')
        print('error: ', err_add, 'threshold', model_dia[true_cls] * 0.1)

        t_est = t_est.T  # * 0.001
        #print('pose: ', pose)
        tDbox = R_gt.dot(ori_points.T).T
        tDbox = tDbox + np.repeat(t_gt[:, np.newaxis], 8, axis=1).T
        box3D = toPix_array(tDbox)
        tDbox = np.reshape(box3D, (16))
        tDbox = tDbox.astype(np.uint16)

        idx = 0
        viz = True
        #if true_cls == 9:
        if viz:

            for hy in range(pose_set.shape[0]):
                # dual quaternion
                #dq = DualQuaternion.from_dq_array(pose_set[hy, :])
                #pose_cls = dq.homogeneous_matrix()
                #R_est = pose_cls[:3, :3]
                #t_est = pose_cls[:3, 3] * -0.001
                # quaternion
                R_est = tf3d.quaternions.quat2mat(pose_set[hy, 3:])
                t_est = pose_set[hy, :3] * 0.001
                # R6d
                # R_est = np.eye(3)
                #R_est[:3, 0] = np.linalg.norm(pose_set[hy, 3:6])
                #R_est[:3, 1] = np.linalg.norm(pose_set[hy, 6:])
                #R_est[:3, 2] = np.linalg.norm(R_est[:3, 0], np.cross(pose_set[hy, 6:]))
                #t_est = pose_set[hy, :3] * 0.001

                eDbox = R_est.dot(ori_points.T).T
                #print(eDbox.shape, np.repeat(t_est, 8, axis=1).T.shape)
                #eDbox = eDbox + np.repeat(t_est, 8, axis=1).T
                eDbox = eDbox + np.repeat(t_est[:, np.newaxis], 8, axis=1).T
                #eDbox = eDbox + np.repeat(t_est, 8, axis=0)
                #print(eDbox.shape)
                est3D = toPix_array(eDbox)
                #print(est3D)
                eDbox = np.reshape(est3D, (16))
                pose = eDbox.astype(np.uint16)
                colGT = (255, 0, 0)

                #R_est = tf3d.quaternions.quat2mat(poses_cls[3:])
                #t_est = poses_cls[:3] * 0.001

                err_add = add(R_est, t_est, R_gt, t_gt, model_vsd["pts"])
                if err_add < model_dia[true_cls] * 0.1:
                    colEst = (0, 204, 0)
                else:
                    colEst = (0, 0, 255)

                image_raw = cv2.line(image_raw, tuple(pose[0:2].ravel()), tuple(pose[2:4].ravel()), colEst, 2)
                image_raw = cv2.line(image_raw, tuple(pose[2:4].ravel()), tuple(pose[4:6].ravel()), colEst, 2)
                image_raw = cv2.line(image_raw, tuple(pose[4:6].ravel()), tuple(pose[6:8].ravel()), colEst, 2)
                image_raw = cv2.line(image_raw, tuple(pose[6:8].ravel()), tuple(pose[0:2].ravel()), colEst, 2)
                image_raw = cv2.line(image_raw, tuple(pose[0:2].ravel()), tuple(pose[8:10].ravel()), colEst, 2)
                image_raw = cv2.line(image_raw, tuple(pose[2:4].ravel()), tuple(pose[10:12].ravel()), colEst, 2)
                image_raw = cv2.line(image_raw, tuple(pose[4:6].ravel()), tuple(pose[12:14].ravel()), colEst, 2)
                image_raw = cv2.line(image_raw, tuple(pose[6:8].ravel()), tuple(pose[14:16].ravel()), colEst, 2)
                image_raw = cv2.line(image_raw, tuple(pose[8:10].ravel()), tuple(pose[10:12].ravel()), colEst,
                                 2)
                image_raw = cv2.line(image_raw, tuple(pose[10:12].ravel()), tuple(pose[12:14].ravel()), colEst,
                                 2)
                image_raw = cv2.line(image_raw, tuple(pose[12:14].ravel()), tuple(pose[14:16].ravel()), colEst,
                                 2)
                image_raw = cv2.line(image_raw, tuple(pose[14:16].ravel()), tuple(pose[8:10].ravel()), colEst,
                                 2)

                #est_points = np.ascontiguousarray(pose_votes, dtype=np.float32).reshape((int(k_hyp * 8), 1, 2))
                corres = np.ascontiguousarray(pose_votes[hy, :], dtype=np.float32).reshape((8, 1, 2))
                #obj_points = np.repeat(ori_points[np.newaxis, :, :], k_hyp, axis=0)
                refer = ori_points.reshape((8, 1, 3))
                retval, orvec, otvec, inliers = cv2.solvePnPRansac(objectPoints=refer,
                                                                   imagePoints=corres, cameraMatrix=K,
                                                                   distCoeffs=None, rvec=None, tvec=None,
                                                                   useExtrinsicGuess=False, iterationsCount=300,
                                                                   reprojectionError=5.0, confidence=0.99,
                                                                   flags=cv2.SOLVEPNP_EPNP)
                R_est, _ = cv2.Rodrigues(orvec)
                t_est = otvec.T
                pose = np.ascontiguousarray(pose_votes[hy, :], dtype=np.float32)
                err_add = add(R_est, t_est, R_gt, t_gt, model_vsd["pts"])
                if err_add < model_dia[true_cls] * 0.1:
                    colEst = (0, 204, 0)
                else:
                    colEst = (0, 0, 255)

                #pose = est_points[idx:idx + 8, 0, :]
                #pose = np.reshape(pose, (16))
                #print(pose.shape, pose)
                image_viz = cv2.line(image_viz, tuple(pose[0:2].ravel()), tuple(pose[2:4].ravel()), colEst, 2)
                image_viz = cv2.line(image_viz, tuple(pose[2:4].ravel()), tuple(pose[4:6].ravel()), colEst, 2)
                image_viz = cv2.line(image_viz, tuple(pose[4:6].ravel()), tuple(pose[6:8].ravel()), colEst, 2)
                image_viz = cv2.line(image_viz, tuple(pose[6:8].ravel()), tuple(pose[0:2].ravel()), colEst, 2)
                image_viz = cv2.line(image_viz, tuple(pose[0:2].ravel()), tuple(pose[8:10].ravel()), colEst, 2)
                image_viz = cv2.line(image_viz, tuple(pose[2:4].ravel()), tuple(pose[10:12].ravel()), colEst, 2)
                image_viz = cv2.line(image_viz, tuple(pose[4:6].ravel()), tuple(pose[12:14].ravel()), colEst, 2)
                image_viz = cv2.line(image_viz, tuple(pose[6:8].ravel()), tuple(pose[14:16].ravel()), colEst, 2)
                image_viz = cv2.line(image_viz, tuple(pose[8:10].ravel()), tuple(pose[10:12].ravel()), colEst,
                                     2)
                image_viz = cv2.line(image_viz, tuple(pose[10:12].ravel()), tuple(pose[12:14].ravel()), colEst,
                                     2)
                image_viz = cv2.line(image_viz, tuple(pose[12:14].ravel()), tuple(pose[14:16].ravel()), colEst,
                                     2)
                image_viz = cv2.line(image_viz, tuple(pose[14:16].ravel()), tuple(pose[8:10].ravel()), colEst,
                                     2)
                idx = idx + 8

            image_raw = cv2.line(image_raw, tuple(tDbox[0:2].ravel()), tuple(tDbox[2:4].ravel()), colGT, 2)
            image_raw = cv2.line(image_raw, tuple(tDbox[2:4].ravel()), tuple(tDbox[4:6].ravel()), colGT, 2)
            image_raw = cv2.line(image_raw, tuple(tDbox[4:6].ravel()), tuple(tDbox[6:8].ravel()), colGT,
                                 2)
            image_raw = cv2.line(image_raw, tuple(tDbox[6:8].ravel()), tuple(tDbox[0:2].ravel()), colGT,
                                 2)
            image_raw = cv2.line(image_raw, tuple(tDbox[0:2].ravel()), tuple(tDbox[8:10].ravel()), colGT,
                                 2)
            image_raw = cv2.line(image_raw, tuple(tDbox[2:4].ravel()), tuple(tDbox[10:12].ravel()), colGT,
                                 2)
            image_raw = cv2.line(image_raw, tuple(tDbox[4:6].ravel()), tuple(tDbox[12:14].ravel()), colGT,
                                 2)
            image_raw = cv2.line(image_raw, tuple(tDbox[6:8].ravel()), tuple(tDbox[14:16].ravel()), colGT,
                                 2)
            image_raw = cv2.line(image_raw, tuple(tDbox[8:10].ravel()), tuple(tDbox[10:12].ravel()),
                                 colGT,
                                 2)
            image_raw = cv2.line(image_raw, tuple(tDbox[10:12].ravel()), tuple(tDbox[12:14].ravel()),
                                 colGT,
                                 2)
            image_raw = cv2.line(image_raw, tuple(tDbox[12:14].ravel()), tuple(tDbox[14:16].ravel()),
                                 colGT,
                                 2)
            image_raw = cv2.line(image_raw, tuple(tDbox[14:16].ravel()), tuple(tDbox[8:10].ravel()),
                                 colGT,
                                 2)

            image_viz = cv2.line(image_viz, tuple(tDbox[0:2].ravel()), tuple(tDbox[2:4].ravel()), colGT, 2)
            image_viz = cv2.line(image_viz, tuple(tDbox[2:4].ravel()), tuple(tDbox[4:6].ravel()), colGT, 2)
            image_viz = cv2.line(image_viz, tuple(tDbox[4:6].ravel()), tuple(tDbox[6:8].ravel()), colGT,
                                 2)
            image_viz = cv2.line(image_viz, tuple(tDbox[6:8].ravel()), tuple(tDbox[0:2].ravel()), colGT,
                                 2)
            image_viz = cv2.line(image_viz, tuple(tDbox[0:2].ravel()), tuple(tDbox[8:10].ravel()), colGT,
                                 2)
            image_viz = cv2.line(image_viz, tuple(tDbox[2:4].ravel()), tuple(tDbox[10:12].ravel()), colGT,
                                 2)
            image_viz = cv2.line(image_viz, tuple(tDbox[4:6].ravel()), tuple(tDbox[12:14].ravel()), colGT,
                                 2)
            image_viz = cv2.line(image_viz, tuple(tDbox[6:8].ravel()), tuple(tDbox[14:16].ravel()), colGT,
                                 2)
            image_viz = cv2.line(image_viz, tuple(tDbox[8:10].ravel()), tuple(tDbox[10:12].ravel()),
                                 colGT,
                                 2)
            image_viz = cv2.line(image_viz, tuple(tDbox[10:12].ravel()), tuple(tDbox[12:14].ravel()),
                                 colGT,
                                 2)
            image_viz = cv2.line(image_viz, tuple(tDbox[12:14].ravel()), tuple(tDbox[14:16].ravel()),
                                 colGT,
                                 2)
            image_viz = cv2.line(image_viz, tuple(tDbox[14:16].ravel()), tuple(tDbox[8:10].ravel()),
                                 colGT,
                                 2)
            image_raw = np.concatenate([image_viz, image_raw], axis=1)
            name = '/home/stefan/PyraPose_viz/detection_' + str(index) + '.jpg'
            #cv2.imwrite(name, image_raw)

        '''
        eDbox = R_best.dot(ori_points.T).T
        eDbox = eDbox + np.repeat(t_best[:, np.newaxis], 8, axis=1).T
        est3D = toPix_array(eDbox)
        eDbox = np.reshape(est3D, (16))
        pose = eDbox.astype(np.uint16)
        colEst = (0, 0, 255)
        image_raw = cv2.line(image_raw, tuple(pose[0:2].ravel()), tuple(pose[2:4].ravel()), colEst, 2)
        image_raw = cv2.line(image_raw, tuple(pose[2:4].ravel()), tuple(pose[4:6].ravel()), colEst, 2)
        image_raw = cv2.line(image_raw, tuple(pose[4:6].ravel()), tuple(pose[6:8].ravel()), colEst, 2)
        image_raw = cv2.line(image_raw, tuple(pose[6:8].ravel()), tuple(pose[0:2].ravel()), colEst, 2)
        image_raw = cv2.line(image_raw, tuple(pose[0:2].ravel()), tuple(pose[8:10].ravel()), colEst, 2)
        image_raw = cv2.line(image_raw, tuple(pose[2:4].ravel()), tuple(pose[10:12].ravel()), colEst, 2)
        image_raw = cv2.line(image_raw, tuple(pose[4:6].ravel()), tuple(pose[12:14].ravel()), colEst, 2)
        image_raw = cv2.line(image_raw, tuple(pose[6:8].ravel()), tuple(pose[14:16].ravel()), colEst, 2)
        image_raw = cv2.line(image_raw, tuple(pose[8:10].ravel()), tuple(pose[10:12].ravel()), colEst,
                             2)
        image_raw = cv2.line(image_raw, tuple(pose[10:12].ravel()), tuple(pose[12:14].ravel()), colEst,
                             2)
        image_raw = cv2.line(image_raw, tuple(pose[12:14].ravel()), tuple(pose[14:16].ravel()), colEst,
                             2)
        image_raw = cv2.line(image_raw, tuple(pose[14:16].ravel()), tuple(pose[8:10].ravel()), colEst,
                             2)

        hyp_mask = np.zeros((640, 480), dtype=np.float32)
        for idx in range(k_hyp):
            hyp_mask[int(est_points[idx, 0, 0]), int(est_points[idx, 0, 1])] += 1
        hyp_mask = np.transpose(hyp_mask)
        hyp_mask = (hyp_mask * (255.0 / np.nanmax(hyp_mask))).astype(np.uint8)
        image_raw[:, :, 0] = np.where(hyp_mask > 0, 0, image_raw[:, :, 0])
        image_raw[:, :, 1] = np.where(hyp_mask > 0, 0, image_raw[:, :, 1])
        image_raw[:, :, 2] = np.where(hyp_mask > 0, hyp_mask, image_raw[:, :, 2])
        '''

        '''
        max_x = int(np.max(est_points[:, :, 0]) + 5)
        min_x = int(np.min(est_points[:, :, 0]) - 5)
        max_y = int(np.max(est_points[:, :, 1]) + 5)
        min_y = int(np.min(est_points[:, :, 1]) - 5)
        print(max_x, min_x, max_y, min_y)
        image_crop = image_raw[min_y:max_y, min_x:max_x, :]
        image_crop = cv2.resize(image_crop, None, fx=2, fy=2)
        '''
        #image_raw = np.concatenate([image_viz, image_raw], axis=1)
        #name = '/home/stefan/PyraPose_viz/detection_' + str(index) + '.jpg'
        #cv2.imwrite(name, image_raw)
            #print('break')

    recall = np.zeros((16), dtype=np.float32)
    precision = np.zeros((16), dtype=np.float32)
    detections = np.zeros((16), dtype=np.float32)
    for i in range(1, (allPoses.shape[0])):
        recall[i] = truePoses[i] / allPoses[i]
        #precision[i] = truePoses[i] / (truePoses[i] + falsePoses[i])
        detections[i] = trueDets[i] / allPoses[i]
        precision[i] = recall[i] / detections[i]

        if np.isnan(recall[i]):
            recall[i] = 0.0
        if np.isnan(precision[i]):
            precision[i] = 0.0
        if np.isnan(detections[i]):
            precision[i] = 0.0

        print('CLS: ', i)
        print('true detections: ', detections[i])
        print('recall: ', recall[i])
        print('precision: ', precision[i])

    recall_all = np.sum(recall[1:]) / 13.0
    precision_all = np.sum(precision[1:]) / 13.0
    detections_all = np.nansum(detections[1:]) / 13.0
    print('ALL: ')
    print('true detections: ', detections_all)
    print('recall: ', recall_all)
    print('precision: ', precision_all)

    print('Errors')
    e_x = np.array(e_x)
    print('e_x: ', np.mean(e_x), np.var(e_x))
    e_y = np.array(e_y)
    print('e_y: ', np.mean(e_y), np.var(e_y))
    e_z = np.array(e_z)
    print('e_z: ', np.mean(e_z), np.var(e_z))
    e_roll = np.array(e_roll)
    print('e_roll: ', np.mean(e_roll), np.var(e_roll))
    e_pitch = np.array(e_pitch)
    print('e_pitch: ', np.mean(e_pitch), np.var(e_pitch))
    e_yaw = np.array(e_yaw)
    print('e_yaw: ', np.mean(e_yaw), np.var(e_yaw))
