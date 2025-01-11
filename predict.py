
import argparse
import torch
import os
import datetime
from tensorboardX import SummaryWriter
import sys
from dataset import find_dataset_def
import torch.backends.cudnn as cudnn
from networks.casmvs import CascadeMVSNet
from networks.ucs import UCSNet
# from networks.casred import Infer_CascadeREDNet
from networks.stsat import ST_SatMVS, Infer_CascadeREDNet
from torch.utils.data import DataLoader
import torch.nn as nn
import time
from tools.utils import *
from dataset.data_io import save_pfm
import matplotlib.pyplot as plt
from networks.emvs import CascadeEMVSNet

cudnn.benchmark = True


parser = argparse.ArgumentParser(description='A PyTorch Implementation')
parser.add_argument('--model', default="samsat", help='select model', choices=['SAMsat', 'red', "casmvs", "ucs", "emvs"])
parser.add_argument('--geo_model', default="rpc", help='select dataset', choices=["rpc", "pinhole"])
parser.add_argument('--use_qc', default=False, help="whether to use Quaternary Cubic Form for RPC warping.")
parser.add_argument('--dataset_root', default=r'F:\data\WHU_TLC\WHU-TLC\open_dataset_rpc\test', help='dataset root')

parser.add_argument('--loadckpt', default=r"F:\Codes2\DC-SatMVS-main\Results\WHU_TLC.ckpt",
                    help='load a specific checkpoint')
# input parameters
parser.add_argument('--view_num', type=int, default=3, help='Number of images.')
parser.add_argument('--ref_view', type=int, default=2)
parser.add_argument('--batch_size', type=int, default=1, help='train batch size')

# Cascade parameters
parser.add_argument('--ndepths', type=str, default="64,32,8", help='ndepths')
parser.add_argument('--min_interval', type=float, default=2.5, help='min_interval in the bottom stage')
parser.add_argument('--depth_inter_r', type=str, default="4,2,1", help='depth_intervals_ratio')
parser.add_argument('--lamb', type=float, default=1.5, help="lamb in ucs-net")
parser.add_argument('--cr_base_chs', type=str, default="8,8,8", help='cost regularization base channels')
parser.add_argument('--gpu_id', type=str, default="0")

# parse arguments and check
args = parser.parse_args()

os.environ["CUDA_VISIBLE_DEVICES"] = args.gpu_id

# print(args.geo_model)
# print(args.dataset_root)
assert args.geo_model in args.dataset_root, Exception("set the wrong data root")
# assert args.geo_model in args.loadckpt, Exception("set the wrong checkpoint")
# assert args.model in args.loadckpt, Exception("set the wrong checkpoint")

def predict():
    print("argv:", sys.argv[1:])
    print_args(args)

    # dataset, dataloader
    MVSDataset = find_dataset_def(args.geo_model)
    # pre_dataset = MVSDataset(args.dataset_root, "pred", args.view_num, ref_view=args.ref_view, use_qc=args.use_qc)
    pre_dataset = MVSDataset(args.dataset_root, "test", args.view_num, ref_view=args.ref_view, use_qc=args.use_qc)

    Pre_ImgLoader = DataLoader(pre_dataset, args.batch_size, shuffle=False, num_workers=0, drop_last=False)

    if args.model == "casmvs":
        model = CascadeMVSNet(min_interval=args.min_interval,
                              ndepths=[int(nd) for nd in args.ndepths.split(",") if nd],
                              depth_interals_ratio=[float(d_i) for d_i in args.depth_inter_r.split(",") if d_i],
                              cr_base_chs=[int(ch) for ch in args.cr_base_chs.split(",") if ch],
                              geo_model=args.geo_model, use_qc=args.use_qc)
        print("===============> Model: Cascade MVS Net ===========>")
    elif args.model == "ucs":
        model = UCSNet(lamb=args.lamb, stage_configs=[int(nd) for nd in args.ndepths.split(",") if nd],
                       base_chs=[int(ch) for ch in args.cr_base_chs.split(",") if ch],
                       geo_model=args.geo_model, use_qc=args.use_qc)
        print("===============> Model: UCS-Net ===========>")
    elif args.model == "red":
        model = Infer_CascadeREDNet(min_interval=args.min_interval,
                                    ndepths=[int(nd) for nd in args.ndepths.split(",") if nd],
                                    depth_interals_ratio=[float(d_i) for d_i in args.depth_inter_r.split(",") if d_i],
                                    cr_base_chs=[int(ch) for ch in args.cr_base_chs.split(",") if ch],
                                    geo_model=args.geo_model, use_qc=args.use_qc)
        print("===============> Model: Cascade RED Net ===========>")
    elif args.model == "samsat":
        model = ST_SatMVS(min_interval=args.min_interval,
                          ndepths=[int(nd) for nd in args.ndepths.split(",") if nd],
                          depth_interals_ratio=[float(d_i) for d_i in args.depth_inter_r.split(",") if d_i],
                          cr_base_chs=[int(ch) for ch in args.cr_base_chs.split(",") if ch],
                          geo_model=args.geo_model, use_qc=args.use_qc)
        print("===============> Model: Our network ===========>")
    elif args.model == "emvs":
        model = CascadeEMVSNet(min_interval=args.min_interval,
                               ndepths=[int(nd) for nd in args.ndepths.split(",") if nd],
                               depth_interals_ratio=[float(d_i) for d_i in args.depth_inter_r.split(",") if d_i],
                               cr_base_chs=[int(ch) for ch in args.cr_base_chs.split(",") if ch],
                               geo_model=args.geo_model, use_qc=args.use_qc)
        print("===============> Model: Cascade EMVS Net ===========>")
    else:
        raise Exception("{}? Not implemented yet!".format(args.model))

    model = nn.DataParallel(model)
    model.cuda()

    # load checkpoint file specified by args.loadckpt
    print("loading model {}".format(args.loadckpt))
    state_dict = torch.load(args.loadckpt)
    model.load_state_dict(state_dict['model'])
    print('Number of model parameters: {}'.format(sum([p.data.nelement() for p in model.parameters()])))

    # create output folder
    # output_folder = os.path.join(args.dataset_root, 'mvs_results')
    output_folder = os.path.join('./mvs_results')
    if not os.path.isdir(output_folder):
        os.mkdir(output_folder)

    avg_test_scalars = DictAverageMeter()
    t0 = time.time()

    idx = 0
    total_time = 0
    for batch_idx, sample in enumerate(Pre_ImgLoader):
        bview = sample['out_view'][0]
        bname = sample['out_name'][0]

        start_time = time.time()
        scalar_outputs, image_outputs = predict_sample(model, sample)
        avg_test_scalars.update(scalar_outputs)
        scalar_outputs = {k: float("{0:.6f}".format(v)) for k, v in scalar_outputs.items()}
        total_time += time.time() - start_time
        print("Iter {}/{}, {}, time = {:3f}, test results = {}".format(batch_idx, len(Pre_ImgLoader), bname, time.time() - start_time, scalar_outputs))

        # save results
        depth_est = np.float32(np.squeeze(tensor2numpy(image_outputs["depth_est"])))
        prob = np.float32(np.squeeze(tensor2numpy(image_outputs["photometric_confidence"])))

        # TODO
        depth_gt = sample['depth']['stage3']
        mask = sample['mask']['stage3']
        depth_gt = np.float32(np.squeeze(tensor2numpy(depth_gt)))
        mask = (np.squeeze(tensor2numpy(mask))).astype(int)
        depth_gt[mask < 0.5] = -999.0

        # paths
        output_folder2 = output_folder + ('/%s/' % bview)

        if not os.path.exists(output_folder2):
            os.mkdir(output_folder2)
        if not os.path.exists(output_folder2 + '/prob/'):
            os.mkdir(output_folder2 + '/prob/')
        if not os.path.exists(output_folder2 + '/init/'):
            os.mkdir(output_folder2 + '/init/')
        if not os.path.exists(output_folder2 + '/prob/color/'):
            os.mkdir(output_folder2 + '/prob/color/')
        if not os.path.exists(output_folder2 + '/init/color/'):
            os.mkdir(output_folder2 + '/init/color/')

        init_depth_map_path = output_folder2 + ('/init/{}.pfm'.format(bname))
        prob_map_path = output_folder2 + ('/prob/{}.pfm'.format(bname))

        # save output
        save_pfm(init_depth_map_path, depth_est)
        save_pfm(prob_map_path, prob)

        if args.geo_model == "pinhole":
            depth_est = np.max(depth_est) - depth_est

        # plt.imshow(depth_est)
        # plt.show()

        plt.imsave(output_folder2 + ('/init/color/{}.png'.format(bname)), depth_est, format='png')
        plt.imsave(output_folder2 + ('/prob/color/{}_prob.png'.format(bname)), prob, format='png')

        del scalar_outputs, image_outputs

    # print("final, time = {:3f}, test results = {}".format(time.time() - t0, avg_test_scalars.mean()))
    print("final, time = {:3f}, test results = {}".format(total_time, avg_test_scalars.mean()))


@make_nograd_func
def predict_sample(model, sample):
    model.eval()

    sample_cuda = tocuda(sample)
    depth_gt_ms = sample_cuda["depth"]
    mask_ms = sample_cuda["mask"]

    num_stage = len([int(nd) for nd in args.ndepths.split(",") if nd])
    depth_gt = depth_gt_ms["stage{}".format(num_stage)]
    mask = mask_ms["stage{}".format(num_stage)]

    outputs = model(sample_cuda["imgs"], sample_cuda["cam_para"], sample_cuda["depth_values"])
    depth_est = outputs["stage3"]["depth"]
    # depth_est = outputs["stage3"]["depth_filtered"]
    photometric_confidence = outputs["stage3"]["photometric_confidence"]

    image_outputs = {"depth_est": depth_est,
                     "photometric_confidence": photometric_confidence,
                     "ref_img": sample["imgs"][:, 0]}

    scalar_outputs = {}

    scalar_outputs["MAE"] = AbsDepthError_metrics(depth_est, depth_gt, mask > 0.5, 250.0)
    scalar_outputs["RMSE"] = RMSE_metrics(depth_est, depth_gt, mask > 0.5, 250.0)
    scalar_outputs["thres1.0m_error"] = Thres_metrics(depth_est, depth_gt, mask > 0.5, 1.0)
    scalar_outputs["thres2.5m_error"] = Thres_metrics(depth_est, depth_gt, mask > 0.5, 2.5)
    scalar_outputs["thres7.5m_error"] = Thres_metrics(depth_est, depth_gt, mask > 0.5, 7.5)

    return tensor2float(scalar_outputs), image_outputs


if __name__ == '__main__':
    predict()


