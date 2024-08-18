#!/usr/bin/env python
# coding=utf-8
# Copyright 2024 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# this code inspired from https://github.com/rohitgandikota/sliders codebase
from diffusers.models.attention_processor import AttnProcessor2_0
from diffusers.models.model_loading_utils import load_model_dict_into_meta
# import jsonlines

import safetensors
import argparse
# import functools
import gc
# import logging
import math
import os
import random
# import shutil
# from pathlib import Path

import accelerate
# import datasets
import numpy as np
import torch
import torch.nn.functional as F
import torch.utils.checkpoint
import transformers
import diffusers

# from diffusers.image_processor import VaeImageProcessor

from accelerate import Accelerator
from accelerate.utils import DistributedDataParallelKwargs, ProjectConfiguration, set_seed
from accelerate.logging import get_logger
# from datasets import load_dataset
# from packaging import version
# from torchvision import transforms
# from torchvision.transforms.functional import crop
from tqdm.auto import tqdm
# from transformers import AutoTokenizer, PretrainedConfig
from diffusers import (
    AutoencoderKL,
    DDPMScheduler,
    EulerDiscreteScheduler,
    DPMSolverMultistepScheduler,
    # DiffusionPipeline,
    UNet2DConditionModel,
)
from pathlib import Path
from diffusers.optimization import get_scheduler
# from diffusers.training_utils import _set_state_dict_into_text_encoder, cast_training_params, compute_snr
from diffusers.training_utils import (
    cast_training_params,
    compute_snr
)
from diffusers.utils import (
    # check_min_version,
    convert_all_state_dict_to_peft,
    convert_state_dict_to_diffusers,
    convert_state_dict_to_kohya,
    convert_unet_state_dict_to_peft,
    is_wandb_available,
)
from diffusers.loaders import LoraLoaderMixin
# from diffusers.utils.import_utils import is_xformers_available
from diffusers.utils.torch_utils import is_compiled_module

from diffusers import StableDiffusionXLPipeline
from kolors.pipelines.pipeline_stable_diffusion_xl_chatglm_256 import StableDiffusionXLPipeline as KolorsPipeline
from tqdm import tqdm 
# from PIL import Image 

from sklearn.model_selection import train_test_split

import json


# import sys
from utils.image_utils_kolors import BucketBatchSampler, CachedImageDataset, create_metadata_cache

# from prodigyopt import Prodigy


# https://github.com/Lightning-AI/pytorch-lightning/blob/0d52f4577310b5a1624bed4d23d49e37fb05af9e/src/lightning_fabric/utilities/seed.py
from random import getstate as python_get_rng_state
from random import setstate as python_set_rng_state

from peft import LoraConfig
from peft.utils import get_peft_model_state_dict, set_peft_model_state_dict
from kolors.models.modeling_chatglm import ChatGLMModel
from kolors.models.tokenization_chatglm import ChatGLMTokenizer
# try:
#     from diffusers.utils import randn_tensor
# except:
#     from diffusers.utils.torch_utils import randn_tensor

if is_wandb_available():
    import wandb
    
from safetensors.torch import save_file

from utils.dist_utils import flush

from hashlib import md5
import glob

from PIL import Image
import os, torch
# from PIL import Image
# from kolors.pipelines.pipeline_stable_diffusion_xl_chatglm_256 import StableDiffusionXLPipeline
from kolors.models.modeling_chatglm import ChatGLMModel
from kolors.models.tokenization_chatglm import ChatGLMTokenizer
from diffusers import UNet2DConditionModel, AutoencoderKL

from utils.image_utils_kolors import compute_text_embeddings
from utils.dist_utils import flush

from utils.utils import get_md5_by_path
from compel import Compel, ReturnedEmbeddingsType
import shutil

import itertools
import re

from mps.calc_mps import MPSModel
from captioner.florenceLargeFt import FlorenceLargeFtModelWrapper
import cv2

# Will error if the minimal version of diffusers is not installed. Remove at your own risks.
# check_min_version("0.30.0.dev0")

logger = get_logger(__name__)
def parse_args(input_args=None):
    parser = argparse.ArgumentParser(description="Simple example of a training script.")
    parser.add_argument(
        "--pretrained_model_name_or_path",
        type=str,
        default=None,
        required=False,
        help="Path to pretrained model or model identifier from huggingface.co/models.",
    )
    parser.add_argument(
        "--model_path",
        type=str,
        default=None,
        help=("seperate model path"),
    )
    parser.add_argument(
        "--train_data_dir",
        type=str,
        default="",
        help=(
            "output folder for the slider training"
        ),
    )
    parser.add_argument(
        "--image_prefix",
        type=str,
        default="image",
        help=(
            "image filename prefix"
        ),
    )
    parser.add_argument(
        "--main_prompt",
        type=str,
        default="a girl",
        help=(
            "the main prompt for both positive images and negative images"
        ),
    )
    # parser.add_argument(
    #     "--uncondition_prompt",
    #     type=str,
    #     default="abstruct",
    #     help=(
    #         "the main uncondition prompt for both positive images and negative images"
    #     ),
    # )
    parser.add_argument(
        "--pos_prompt",
        type=str,
        default="beatiful",
        help=(
            "positive images generation prompt to describe the main subject"
        ),
    )
    parser.add_argument(
        "--neg_prompt",
        type=str,
        default="ugly",
        help=(
            "negative images generation prompt to describe the main subject"
        ),
    )
    parser.add_argument(
        "--caption_prefix",
        type=str,
        default="anime artwork",
        help=(
            "caption prefix after florencelarge generated content"
        ),
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=50,
        help=(
            "Image generation steps"
        ),
    )
    parser.add_argument(
        "--cfg",
        type=float,
        default=3.5,
        help=(
            "Image generation guidance_scale"
        ),
    )
    parser.add_argument(
        "--generation_batch",
        type=int,
        default=10,
        help=(
            "Total batch of generation"
        ),
    )
    parser.add_argument(
        "--vae_path",
        type=str,
        default=None,
        help=("seperate vae path"),
    )
    parser.add_argument(
        "--is_kolors",
        default=True,
        action="store_true",
        help=("if pipeline is kolors"),
    )
    # parser.add_argument(
    #     "--batch_size",
    #     type=int,
    #     default=1,
    #     help=(
    #         "batch size of generation"
    #     ),
    # )
    
    parser.add_argument("--seed", type=int, default=None, help="A seed for generation init.")
    
    if input_args is not None:
        args = parser.parse_args(input_args)
    else:
        args = parser.parse_args()

    return args

# def remove_non_ascii(text):
#     return re.sub(r'[^\x00-\x7F]*[\ ]', '_', text)


def clean_text(text):
    return ''.join([char if ord(char) < 128 else '' for char in text])

def handle_character_name(text):
    clear_text = text.replace("1girl,","").replace("1boy,","").replace("1other","").replace("male focus,","")
    clear_text = clear_text.replace("\\","").replace("(","_").replace(")","").replace(" ","").replace(",","_")
    return clean_text(clear_text)

def handle_replace(result):
    result = re.sub(r'A cartoon[a-zA-Z ]*?of ', '', result)
    result = re.sub(r'An animated[a-zA-Z ]*?of ', '', result)
    return result

@torch.no_grad()
def main(args):
    # args.train_data_dir = "F:/ImageSet/kolors_anime"
    # args.train_data_dir = "F:/ImageSet/pony_caption"
    args.output_dir = "F:/ImageSet/pony_caption_output_test"
    
    
    # If the destination directory doesn't exist, create it
    # if os.path.exists(args.output_dir):
    #     # remove output_dir
    #     shutil.rmtree(args.output_dir)
    #     supported_image_types = ['.webp']
    #     files = glob.glob(f"{args.output_dir}/**", recursive=True)
    #     for file in files:
    #         # remove file
    #         if os.path.isfile(file) and os.path.splitext(file)[1] in supported_image_types:
    #             os.remove(file)
    os.makedirs(args.output_dir,exist_ok=True)

    # Copy the entire directory tree to the new location
    # shutil.copytree(args.train_data_dir, args.output_dir, dirs_exist_ok=True)
    
    output_dir = args.output_dir
    
    args.image_prefix = "anime"
    
    args.male_character_list = "F:/T2ITrainer/generation/male_character_list_test.txt"
    args.female_character_list = "F:/T2ITrainer/generation/female_character_list_test.txt"
    # args.main_prompt = "photo of sky"
    # args.uncondition_prompt = "star, starry, oil painting"
    
    # args.model_path = "F:/models/unet/OpenKolors_v1_4_beta.safetensors"
    # args.pos_prompt = "半身图，二次元同人志动漫风格，小红书照片，keta style,modare style, yunamaro style，{prompt}，soft color, soft lighting rendering, 低饱和度，低对比度，照射在她幸福的脸庞上，动态角度，倾斜角度拍摄高生动，丰富的细节 高度细节 精致的灯光和阴影 丰富的背景"
    # args.neg_prompt = "strong brightness, strong contrast，红色调，蓝色调，简单的背景，微距拍摄，阳光，高光，过曝，杂乱的线条，凌乱的头发，油画，灯笼，红灯笼，复古"
    
    args.is_kolors = False
    # args.model_path = "F:/models/Stable-diffusion/sdxl/ponyDiffusionV6XL_v6StartWithThisOne.safetensors"
    args.model_path = "F:/models/Stable-diffusion/sdxl/tPonynai3_v6.safetensors"
    args.pos_prompt = "score_9, score_8_up, score_7_up, score_6_up, score_5_up, score_4_up, source_anime, anime"
    # args.neg_prompt = "weapon, sword, katana, score_6, score_5, score_4,  source_pony, source_furry, child, loli, deformed, bad anatomy, disfigured, poorly drawn face, watermark, web adress, mutated, extra limb, ugly, poorly drawn hands, missing limb, floating limbs, disconnected limbs, disconnected head, malformed hands, long neck, mutated hands and fingers, bad hands, missing fingers, worst quality, low quality, mutation, poorly drawn, huge calf, bad hands, fused hand, missing hand, disappearing arms, disappearing thigh, disappearing calf, disappearing legs, missing fingers, fused fingers, abnormal eye proportion, abnormal hands, abnormal legs, abnormal feet, abnormal fingers, noisy, deformed, ugly, text, abstract"
    args.neg_prompt = "score_6, score_5, score_4,  source_pony, source_furry, deformed, bad anatomy, disfigured, poorly drawn face, watermark, web adress, mutated, extra limb, ugly, poorly drawn hands, missing limb, floating limbs, disconnected limbs, disconnected head, malformed hands, long neck, mutated hands and fingers, bad hands, missing fingers, worst quality, low quality, mutation, poorly drawn, "
    args.caption_prefix = "二次元动漫风格, anime artwork"
    # args.batch_size = 2
    args.generation_batch = 1
    args.pretrained_model_name_or_path = "F:/T2ITrainer/kolors_models"
    args.steps = 30
    args.cfg = 5
    args.seed = 34652
    args.vae_path = "F:/models/VAE/sdxl_vae.safetensors"
    
    # main_prompt = args.main_prompt
    # uncondition_prompt = args.uncondition_prompt
    pos_prompt = args.pos_prompt
    neg_prompt = args.neg_prompt
    # batch_size = args.batch_size
    generation_batch = args.generation_batch
    ckpt_dir = args.pretrained_model_name_or_path
    steps = args.steps
    cfg = args.cfg
    
    # random seed
    # if args.seed == -1:
    #     seed = random.randint(0, 1000)
    #     print(f"set random seed: {seed}")
    # else:
    #     seed = args.seed
    # seed = args.seed
    seed = random.randint(0, 10000)
    
    # clothing_dir = "F:/T2ITrainer/generation"
    # clothing_file_list = [
    #     # "c_bottomwear.txt", "c_clothing.txt", "c_topwear.txt"
    #     "c_test.txt"
    # ]
    # clothing_list = []
    # for clothing_file in clothing_file_list:
    #     clothing_path = os.path.join(clothing_dir, clothing_file)
    #     with open(clothing_path, "r", encoding="utf-8") as f:
    #         content = f.read()
    #         clothing_list += content.split(",")
    
    metadata_file = "metadata_kolors_slider_multiple.json"
    metadata_path = os.path.join(output_dir, metadata_file)
    metadata = {
        # 'main_prompt':main_prompt,
        # 'uncondition_prompt':uncondition_prompt,
        # 'pos_prompt':pos_prompt,
        'neg_prompt':neg_prompt,
        'generation_batch':generation_batch,
        'pretrained_model_name_or_path':ckpt_dir,
        'steps':steps,
        'cfg':cfg,
        'seed':seed,
        'images':[]
    }
    
    # male_character_list = []
    # # read from args.male_character_list
    # with open(args.male_character_list, "r") as f:
    #     for line in f.readlines():
    #         male_character_list.append(line.strip())

    # female_character_list = []
    # with open(args.female_character_list, "r") as f:
    #     for line in f.readlines():
    #         female_character_list.append(line.strip())
    
    # freeze rng
    np.random.seed(seed)
    torch.manual_seed(seed)
    resolution = 1024
    
    device = torch.device("cuda")
    weight_dtype = torch.float16
    # vae = AutoencoderKL.from_pretrained(f"{ckpt_dir}/vae").half()
    vae_folder = os.path.join(args.pretrained_model_name_or_path, "vae")
    if args.vae_path:
        vae = AutoencoderKL.from_single_file(
            args.vae_path,
            config=vae_folder,
        )
    else:
        # load from repo
        weight_file = "diffusion_pytorch_model"
        vae_variant = None
        ext = ".safetensors"
        # diffusion_pytorch_model.fp16.safetensors
        fp16_weight = os.path.join(vae_folder, f"{weight_file}.fp16{ext}")
        fp32_weight = os.path.join(vae_folder, f"{weight_file}{ext}")
        if os.path.exists(fp16_weight):
            vae_variant = "fp16"
        elif os.path.exists(fp32_weight):
            vae_variant = None
        else:
            raise FileExistsError(f"{fp16_weight} and {fp32_weight} not found. \n Please download the model from https://huggingface.co/Kwai-Kolors/Kolors or https://hf-mirror.com/Kwai-Kolors/Kolors")
            
        vae = AutoencoderKL.from_pretrained(
                args.pretrained_model_name_or_path, variant=vae_variant
            )

    vae.to(device, dtype=weight_dtype)
    vae.requires_grad_(False)
    compel = None
    if args.is_kolors:
        text_encoder = ChatGLMModel.from_pretrained(
            f'{ckpt_dir}/text_encoder',
            torch_dtype=torch.float16).half().to(device)
        text_encoder.requires_grad_(False)
        text_encoder.to(device, dtype=weight_dtype)
        tokenizer = ChatGLMTokenizer.from_pretrained(f'{ckpt_dir}/text_encoder')
        
        # scheduler = EulerDiscreteScheduler.from_pretrained(f"{ckpt_dir}/scheduler")
        scheduler = DPMSolverMultistepScheduler.from_pretrained(f"{ckpt_dir}/scheduler")
        # unet = UNet2DConditionModel.from_pretrained(f"{ckpt_dir}/unet")
        # unet.to(device, dtype=weight_dtype)
        # unet.requires_grad_(False)
        
        
        # load from repo
        if args.pretrained_model_name_or_path == "Kwai-Kolors/Kolors":
            unet = UNet2DConditionModel.from_pretrained(
                    args.pretrained_model_name_or_path, subfolder="unet", variant="fp16"
                ).to(device, dtype=weight_dtype)
        else:
            # load from repo
            unet_folder = os.path.join(args.pretrained_model_name_or_path, "unet")
            weight_file = "diffusion_pytorch_model"
            unet_variant = None
            ext = ".safetensors"
            # diffusion_pytorch_model.fp16.safetensors
            fp16_weight = os.path.join(unet_folder, f"{weight_file}.fp16{ext}")
            fp32_weight = os.path.join(unet_folder, f"{weight_file}{ext}")
            if os.path.exists(fp16_weight):
                unet_variant = "fp16"
            elif os.path.exists(fp32_weight):
                unet_variant = None
            else:
                raise FileExistsError(f"{fp16_weight} and {fp32_weight} not found. \n Please download the model from https://huggingface.co/Kwai-Kolors/Kolors or https://hf-mirror.com/Kwai-Kolors/Kolors")
                
            unet = UNet2DConditionModel.from_pretrained(
                        unet_folder, variant=unet_variant
                    ).to(device, dtype=weight_dtype)
        
        if not (args.model_path is None or args.model_path == ""):
            # load from file
            state_dict = safetensors.torch.load_file(args.model_path, device="cpu")
            unexpected_keys = load_model_dict_into_meta(
                unet,
                state_dict,
                device=device,
                dtype=weight_dtype,
                model_name_or_path=args.model_path,
            )
            # updated_state_dict = unet.state_dict()
            if len(unexpected_keys) > 0:
                print(f"Unexpected keys in state_dict: {unexpected_keys}")
            unet.to(device, dtype=weight_dtype)
            del state_dict,unexpected_keys
            flush()
    
    # pipe = OriStableDiffusionXLPipeline.from_pretrained(
    #     "stabilityai/stable-diffusion-xl-base-1.0", 
    #     torch_dtype=torch.float16
    # )
        pipe = KolorsPipeline(
            vae=vae,
            text_encoder=text_encoder,
            tokenizer=tokenizer,
            unet=unet,
            scheduler=scheduler,
            force_zeros_for_empty_prompt=False
        ).to("cuda")
        pipe.enable_model_cpu_offload()
        
        
        # cache negative prompt to train_data_dir
        # for negative
    else: 
        
        pipe = StableDiffusionXLPipeline.from_single_file(
            args.model_path,variant="fp16", use_safetensors=True, 
            torch_dtype=torch.float16).to("cuda")
        
        pipe.unet.to(device, dtype=weight_dtype)
        pipe.vae=vae
        
        compel = Compel(tokenizer=[pipe.tokenizer, pipe.tokenizer_2] , text_encoder=[pipe.text_encoder, pipe.text_encoder_2], returned_embeddings_type=ReturnedEmbeddingsType.PENULTIMATE_HIDDEN_STATES_NON_NORMALIZED, requires_pooled=[False, True])
        prompt_embeds, pooled_prompt_embeds = compel(neg_prompt)
        
        scheduler = DPMSolverMultistepScheduler(
            beta_start=0.00085, beta_end=0.012, beta_schedule="scaled_linear", num_train_timesteps=1000, 
            use_karras_sigmas=True,algorithm_type='dpmsolver++',solver_order=2
        )

        # scheduler = DEISMultistepScheduler(
        #     beta_start=0.00085, beta_end=0.012, beta_schedule="scaled_linear", num_train_timesteps=1000, 
        #     solver_order=3
        # )

        pipe.scheduler = scheduler
    
    neg_npz_path = f"{output_dir}/negative.npkolors"
    if os.path.exists(neg_npz_path):
        # load file
        neg_npz_dict = torch.load(neg_npz_path)
    else:
        if args.is_kolors:
            prompt_embeds, pooled_prompt_embeds = compute_text_embeddings([text_encoder],[tokenizer],neg_prompt,device=text_encoder.device)
        else:
            prompt_embeds, pooled_prompt_embeds = compel(neg_prompt)
        prompt_embed = prompt_embeds.squeeze(0)
        pooled_prompt_embed = pooled_prompt_embeds.squeeze(0)
        # save embeddings
        neg_npz_dict = {
            "prompt_embed": prompt_embed.cpu(), 
            "pooled_prompt_embed": pooled_prompt_embed.cpu(),
        }
        # save latent to cache file
        torch.save(neg_npz_dict, neg_npz_path)
    
    
    # read files for prompt
    # files = glob.glob(f"{output_dir}/**", recursive=True)
    # text_files = [f for f in files if os.path.splitext(f)[-1].lower() in supported_image_types]

    generation_configs = [
        {
            "dir_name" : "female",
            "character_path":"F:/T2ITrainer/generation/female_character_list_test_one.json",
            "prompt_list":[
                {
                    "name":"background",
                    "path":"F:/T2ITrainer/generation/c_background_test.json"
                },
                {
                    "name":"cloth",
                    "path":"F:/T2ITrainer/generation/c_female_cloth_test.json"
                },
                {
                    "name":"cameraAngle",
                    "path":"F:/T2ITrainer/generation/c_angle_test.json"
                },
                {
                    "name":"solo",
                    "path":"F:/T2ITrainer/generation/c_solo.json"
                },
                {
                    "name":"action",
                    "path":"F:/T2ITrainer/generation/c_action_test.json"
                },
                {
                    "name":"lookAt",
                    "path":"F:/T2ITrainer/generation/c_lookat.json"
                },
            ]
        },
        # {
        #     "dir_name" : "male",
        #     "character_path":"F:/T2ITrainer/generation/male_character_list_test_one.json",
        #     "prompt_list":[
        #         {
        #             "name":"background",
        #             "path":"F:/T2ITrainer/generation/c_background_test.json"
        #         },
        #         {
        #             "name":"cloth",
        #             "path":"F:/T2ITrainer/generation/c_male_cloth_test.json"
        #         },
        #         {
        #             "name":"cameraAngle",
        #             "path":"F:/T2ITrainer/generation/c_angle_test.json"
        #         },
        #         {
        #             "name":"solo",
        #             "path":"F:/T2ITrainer/generation/c_solo.json"
        #         },
        #         {
        #             "name":"action",
        #             "path":"F:/T2ITrainer/generation/c_action_test.json"
        #         },
        #         {
        #             "name":"lookAt",
        #             "path":"F:/T2ITrainer/generation/c_lookat.json"
        #         }
        #     ]
        # }
    ]
    # height, width
    resolutions = [(1024, 1024),(1344, 768),(1344,1344)]
    # enlarge text_files
    for generation_config in generation_configs:
        all_lists = []
        dir_name = generation_config["dir_name"]
        output_subdir = f"{output_dir}/{dir_name}"
        os.makedirs(output_subdir, exist_ok=True)
        prompt_list = generation_config["prompt_list"]
        
        for prompt_config in prompt_list:
            path = prompt_config["path"]
            # print(path)
            items=[]
            with open(path, "r", encoding='utf-8') as readfile:
                items = json.loads(readfile.read())
            all_lists.append(items)
        combinations = itertools.product(*all_lists)
            
        character_path = generation_config["character_path"]
        with open(character_path, "r", encoding='utf-8') as readfile:
            characters = json.loads(readfile.read())
        
        for character in characters:
            clear_character = handle_character_name(character)
            output_character_dir = f"{output_subdir}/{clear_character}" 
            os.makedirs(output_character_dir, exist_ok=True)
            for i,combination in enumerate(combinations):
                actual_prompt = f"{pos_prompt}, {character}, {', '.join(combination)}"
                text_path = f"{output_character_dir}/{i}_prompt.txt"
                if not os.path.exists(text_path):
                    # write actual prompt to text path
                    with open(text_path, "w", encoding='utf-8') as writefile:
                        # save file
                        writefile.write(actual_prompt)
            
    # read file agains
    supported_image_types = ['.txt']
    files = glob.glob(f"{output_dir}/**", recursive=True)
    text_files = [f for f in files if os.path.splitext(f)[-1].lower() in supported_image_types and "_res_" not in f]
 
    for text_file in tqdm(text_files):
        npz_path = text_file.replace(".txt",".npkolors")
        prompt = ""
        with open(text_file, 'r', encoding="utf-8") as f:
            prompt = f.read()
        # prompt = pos_prompt.format(prompt = content)
        if os.path.exists(npz_path):
            metadata["images"].append({
                "prompt":prompt,
                'npz_path_md5':get_md5_by_path(npz_path),
                "npz_path":npz_path,
                "txt_path":text_file
            })
            continue
        if args.is_kolors:
            # for positive images generation
            prompt_embeds, pooled_prompt_embeds = compute_text_embeddings([text_encoder],[tokenizer],prompt,device=text_encoder.device)
        else:
            prompt_embeds, pooled_prompt_embeds = compel(prompt)
        prompt_embed = prompt_embeds.squeeze(0)
        pooled_prompt_embed = pooled_prompt_embeds.squeeze(0)
        # save embeddings
        npz_dict = {
            "prompt_embed": prompt_embed.cpu(), 
            "pooled_prompt_embed": pooled_prompt_embed.cpu(),
        }
        # save latent to cache file
        torch.save(npz_dict, npz_path)
        metadata["images"].append({
            "prompt":prompt,
            'npz_path_md5':get_md5_by_path(npz_path),
            "npz_path":npz_path,
            "txt_path":text_file
        })
        
        
    if args.is_kolors:
        text_encoder.to("cpu")
        # tokenizer.to("cpu")
        del text_encoder, tokenizer
    else:
        del pipe.tokenizer, pipe.tokenizer_2, pipe.text_encoder, pipe.text_encoder_2
        pipe.tokenizer = None
        pipe.tokenizer_2 = None
        pipe.text_encoder = None
        pipe.text_encoder_2 = None
    flush()
    uncondition_prompt_embeds = torch.stack([neg_npz_dict['prompt_embed']])
    uncondition_pooled_prompt_embeds = torch.stack([neg_npz_dict['pooled_prompt_embed']])
    
    torch.backends.cuda.matmul.allow_tf32 = True
    # pipe.enable_sequential_cpu_offload()
    pipe.enable_vae_tiling()
    mps_model = MPSModel()
    with torch.no_grad():
        for config in tqdm(metadata["images"]):
            prompt = config["prompt"]
            pure_prompt = prompt.replace(f"{pos_prompt}","")
            text_file = config["txt_path"]
            latent_path = text_file.replace(".txt",".npkolors")
            sample_seed = seed
            # load npz_path
            npz_dict = torch.load(config["npz_path"])
            prompt_embeds = torch.stack([npz_dict['prompt_embed']])
            pooled_prompt_embeds = torch.stack([npz_dict['pooled_prompt_embed']])
            # for i in range(generation_batch):
            # use opposite prompt as negative prompt 
            
            for resolution in resolutions:
                image_path = text_file.replace(".txt",f"_res_{resolution[0]}x{resolution[1]}.webp")
                new_text_file = image_path.replace(".webp",f".txt")
                if not os.path.exists(image_path):
                    if args.is_kolors:
                        output,latent = pipe(
                            prompt_embeds=prompt_embeds.to(device), 
                            pooled_prompt_embeds=pooled_prompt_embeds.to(device), 
                            negative_prompt_embeds=uncondition_prompt_embeds.to(device),
                            negative_pooled_prompt_embeds=uncondition_pooled_prompt_embeds.to(device),
                            height=resolution[0],
                            width=resolution[1],
                            num_inference_steps=steps,
                            guidance_scale=cfg,
                            num_images_per_prompt=1,
                            generator= torch.Generator(pipe.device).manual_seed(sample_seed),
                            )
                        # save latent
                        time_id = torch.tensor(list((resolution[0], resolution[1]) + 
                                                    (0,0) + 
                                                    (resolution[0], resolution[1]))).to(vae.device, dtype=vae.dtype)
                        latent_dict = {
                            'latent': latent[0].cpu(),
                            'time_id': time_id.cpu(),
                        }
                        torch.save(latent_dict, latent_path)
                        del latent
                    else:
                        output = pipe(
                            prompt_embeds=prompt_embeds.to(device), 
                            pooled_prompt_embeds=pooled_prompt_embeds.to(device), 
                            negative_prompt_embeds=uncondition_prompt_embeds.to(device),
                            negative_pooled_prompt_embeds=uncondition_pooled_prompt_embeds.to(device),
                            height=resolution[0],
                            width=resolution[1],
                            num_inference_steps=steps,
                            guidance_scale=cfg,
                            num_images_per_prompt=1,
                            generator= torch.Generator(device=device).manual_seed(sample_seed),
                        )
                
                    # save image
                    image = output.images[0]
                
                    print("\n")
                    print(image_path)
                    image.save(image_path)
                else:
                    image = Image.open(image_path)
                
                score = mps_model.score(image,pure_prompt).item()
                config["mps_score"] = score
                score_prompt = " score_10_down"
                if score > 10:
                    score_prompt = "score_10_up"
                if score > 15:
                    score_prompt = "score_15_up"
                pure_prompt += f", {score_prompt}"
                # write actual prompt to text path
                with open(new_text_file, "w", encoding='utf-8') as writefile:
                    # save file
                    writefile.write(pure_prompt)
                # write pure_prompt to new text file
                
                print(config["mps_score"])
                
                sample_seed += 100
                del output, image
                flush()  
            
            # break
                
    # save metadata
    with open(metadata_path, "w", encoding='utf-8') as writefile:
        writefile.write(json.dumps(metadata, indent=4))

    
    del pipe
    flush()
    
    captioner = FlorenceLargeFtModelWrapper()
    
    files = glob.glob(f"{output_dir}/**", recursive=True)
    image_exts = [".png",".jpg",".jpeg",".webp"]
    image_files = [f for f in files if os.path.splitext(f)[-1].lower() in image_exts and "_ori" not in f]
    for image_file in tqdm(image_files):
        text_file = os.path.splitext(image_file)[0] + ".txt"
        # image_path = os.path.join(input_dir, image_file)
        
        image = cv2.imread(image_file)
        result = captioner.execute(image)
        
        result = handle_replace(result)
        
        # read text file
        with open(text_file, "r", encoding="utf-8") as f:
            text = f.read()
            new_content = f"{args.caption_prefix}, {result}, {text}"
            # rename original text file to _ori.txt
            old_text_file = text_file.replace(".txt","_ori.txt")
            if os.path.exists(old_text_file):
                continue
            # save new content to text file
            with open(old_text_file, "w", encoding="utf-8") as ori_f:
                ori_f.write(text)
                print("save ori content to text file: ", old_text_file)
            # save new content to text file
            with open(text_file, "w", encoding="utf-8") as new_f:
                new_f.write(new_content)
                print("save new content to text file: ", text_file)
            
        

if __name__ == "__main__":
    args = parse_args()
    main(args)