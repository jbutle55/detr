# Copyright (c) Facebook, Inc. and its affiliates. All Rights Reserved
"""
DETR Training Script.

This script is a simplified version of the training script in detectron2/tools.
"""
import os
import sys
import itertools

# fmt: off
sys.path.insert(1, os.path.join(sys.path[0], '..'))
# fmt: on

import time
from typing import Any, Dict, List, Set

import torch

import detectron2.utils.comm as comm
from d2.detr import DetrDatasetMapper, add_detr_config
from detectron2.checkpoint import DetectionCheckpointer
from detectron2.config import get_cfg
from detectron2.data import MetadataCatalog, build_detection_train_loader
from detectron2.engine import DefaultTrainer, default_argument_parser, default_setup, launch
from detectron2.evaluation import COCOEvaluator, verify_results
from detectron2.data.datasets import register_coco_instances
from detectron2.solver.build import maybe_add_gradient_clipping

register_coco_instances('uav_dataset1', {},
                            'uav-detect/cars-only/dataset1/dataset1_x1y1wh.json',
                            'uav-detect/cars-only/dataset1/images')
register_coco_instances('uav_dataset2', {},
                        'uav-detect/cars-only/dataset2/dataset2_x1y1wh.json',
                        'uav-detect/cars-only/dataset2/images')
register_coco_instances('uav_dataset3', {},
                        'uav-detect/cars-only/dataset3/dataset3_x1y1wh.json',
                        'uav-detect/cars-only/dataset3/images')
register_coco_instances('uav_dataset4', {},
                        'uav-detect/cars-only/dataset4/dataset4_x1y1wh.json',
                        'uav-detect/cars-only/dataset4/images')
register_coco_instances('shapes_train', {},
                        'shapes/Shapes_7500imgs_mod4/shapes.json',
                        'shapes/Shapes_7500imgs_mod4/images')
register_coco_instances('shapes_val_no_gauss', {},
                        'shapes/Shapes_1500imgs/no_gauss/shapes.json',
                        'shapes/Shapes_1500imgs/no_gauss/images')
register_coco_instances('aerial_train_cars', {},
                            'aerial-cars/cars-only/labels/aerial_train.json',
                            'aerial-cars/cars-only/images/train')
register_coco_instances('aerial_valid_cars', {},
                        'aerial-cars/cars-only/labels/aerial_valid.json',
                        'aerial-cars/cars-only/images/valid')


class Trainer(DefaultTrainer):
    """
    Extension of the Trainer class adapted to DETR.
    """

    @classmethod
    def build_evaluator(cls, cfg, dataset_name, output_folder=None):
        """
        Create evaluator(s) for a given dataset.
        This uses the special metadata "evaluator_type" associated with each builtin dataset.
        For your own dataset, you can simply create an evaluator manually in your
        script and do not have to worry about the hacky if-else logic here.
        """
        if output_folder is None:
            output_folder = os.path.join(cfg.OUTPUT_DIR, "inference")
        return COCOEvaluator(dataset_name, cfg, True, output_folder)

    @classmethod
    def build_train_loader(cls, cfg):
        if "Detr" == cfg.MODEL.META_ARCHITECTURE:
            mapper = DetrDatasetMapper(cfg, True)
        else:
            mapper = None
        return build_detection_train_loader(cfg, mapper=mapper)

    @classmethod
    def build_optimizer(cls, cfg, model):
        params: List[Dict[str, Any]] = []
        memo: Set[torch.nn.parameter.Parameter] = set()
        for key, value in model.named_parameters(recurse=True):
            if not value.requires_grad:
                continue
            # Avoid duplicating parameters
            if value in memo:
                continue
            memo.add(value)
            lr = cfg.SOLVER.BASE_LR
            weight_decay = cfg.SOLVER.WEIGHT_DECAY
            if "backbone" in key:
                lr = lr * cfg.SOLVER.BACKBONE_MULTIPLIER
            params += [{"params": [value], "lr": lr, "weight_decay": weight_decay}]

        def maybe_add_full_model_gradient_clipping(optim):  # optim: the optimizer class
            # detectron2 doesn't have full model gradient clipping now
            clip_norm_val = cfg.SOLVER.CLIP_GRADIENTS.CLIP_VALUE
            enable = (
                cfg.SOLVER.CLIP_GRADIENTS.ENABLED
                and cfg.SOLVER.CLIP_GRADIENTS.CLIP_TYPE == "full_model"
                and clip_norm_val > 0.0
            )

            class FullModelGradientClippingOptimizer(optim):
                def step(self, closure=None):
                    all_params = itertools.chain(*[x["params"] for x in self.param_groups])
                    torch.nn.utils.clip_grad_norm_(all_params, clip_norm_val)
                    super().step(closure=closure)

            return FullModelGradientClippingOptimizer if enable else optim

        optimizer_type = cfg.SOLVER.OPTIMIZER
        if optimizer_type == "SGD":
            optimizer = maybe_add_full_model_gradient_clipping(torch.optim.SGD)(
                params, cfg.SOLVER.BASE_LR, momentum=cfg.SOLVER.MOMENTUM
            )
        elif optimizer_type == "ADAMW":
            optimizer = maybe_add_full_model_gradient_clipping(torch.optim.AdamW)(
                params, cfg.SOLVER.BASE_LR
            )
        else:
            raise NotImplementedError(f"no optimizer type {optimizer_type}")
        if not cfg.SOLVER.CLIP_GRADIENTS.CLIP_TYPE == "full_model":
            optimizer = maybe_add_gradient_clipping(cfg, optimizer)
        return optimizer


def setup(args):
    """
    Create configs and perform basic setups.
    """
    cfg = get_cfg()
    add_detr_config(cfg)
    cfg.merge_from_file(args.config_file)
    cfg.merge_from_list(args.opts)
    cfg.freeze()
    default_setup(cfg, args)
    return cfg


def main(args):
    cfg = setup(args)

    print(f'cfg: {cfg}')

    if args.eval_only:
        model = Trainer.build_model(cfg)
        DetectionCheckpointer(model, save_dir=cfg.OUTPUT_DIR).resume_or_load(cfg.MODEL.WEIGHTS, resume=args.resume)
        res = Trainer.test(cfg, model)
        if comm.is_main_process():
            verify_results(cfg, res)
        return res

    trainer = Trainer(cfg)
    trainer.resume_or_load(resume=args.resume)
    return trainer.train()


if __name__ == "__main__":
    args = default_argument_parser().parse_args()
    print("Command Line Args:", args)
    launch(
        main,
        args.num_gpus,
        num_machines=args.num_machines,
        machine_rank=args.machine_rank,
        dist_url=args.dist_url,
        args=(args,),
    )


# python ~/Scripts/detr/d2/train_net.py --config ~/Scripts/detr/d2/configs/detr_256_6_6_torchvision_custom.yaml --num-gpus 1 MODEL.WEIGHTS "/home/justin.butler1/Scripts/detr/converted_models/converted_detr_r101_model.pth" SOLVER.IMS_PER_BATCH 5 OUTPUT_DIR 'detr_testing'
