# --------------------------------------------------------
# OpenVQA
# Licensed under The MIT License [see LICENSE for details]
# Written by Zhenwei Shao https://github.com/ParadoxZW
# --------------------------------------------------------

from openvqa.ops.fc import FC, MLP
from openvqa.ops.layer_norm import LayerNorm
from openvqa.models.ban.ban import BAN

import torch.nn as nn
import torch.nn.functional as F
from torch.nn.utils.weight_norm import weight_norm
import torch


# ------------------------------
# ---- Flatten the sequence ----
# ------------------------------

class AttFlat(nn.Module):
    def __init__(self, __C):
        super(AttFlat, self).__init__()
        self.__C = __C

        self.mlp = MLP(
            in_size=__C.HIDDEN_SIZE,
            mid_size=__C.FLAT_MLP_SIZE,
            out_size=__C.FLAT_GLIMPSES,
            dropout_r=__C.DROPOUT_R,
            use_relu=True
        )

        self.linear_merge = nn.Linear(
            __C.HIDDEN_SIZE * __C.FLAT_GLIMPSES,
            __C.FLAT_OUT_SIZE
        )

    def forward(self, x, x_mask):
        att = self.mlp(x)
        att = att.masked_fill(
            x_mask.squeeze(1).squeeze(1).unsqueeze(2),
            -1e9
        )
        att = F.softmax(att, dim=1)

        att_list = []
        for i in range(self.__C.FLAT_GLIMPSES):
            att_list.append(
                torch.sum(att[:, :, i: i + 1] * x, dim=1)
            )

        x_atted = torch.cat(att_list, dim=1)
        x_atted = self.linear_merge(x_atted)

        return x_atted


# -------------------------
# ---- Main MCAN Model ----
# -------------------------

class Net(nn.Module):
    def __init__(self, __C, pretrained_emb, token_size, answer_size):
        super(Net, self).__init__()
        self.__C = __C

        self.embedding = nn.Embedding(
            num_embeddings=token_size,
            embedding_dim=__C.WORD_EMBED_SIZE
        )

        # Loading the GloVe embedding weights
        if __C.USE_GLOVE:
            self.embedding.weight.data.copy_(torch.from_numpy(pretrained_emb))

        self.rnn = nn.GRU(
            input_size=__C.WORD_EMBED_SIZE,
            hidden_size=__C.HIDDEN_SIZE,
            num_layers=1,
            batch_first=True
        )

        # if __C.FEATURE['FRCN_FEATURE']:
        #     frcn_linear_size = __C.FEATURE['FRCNFEAT_SIZE']
        #     if __C.FEATURE['SPATIAL_FEATURE']:
        #         self.spatfeat_linear = nn.Linear(5, __C.FEATURE['SPATFEAT_EMB_SIZE'])
        #         frcn_linear_size += __C.FEATURE['SPATFEAT_EMB_SIZE']
        #     self.frcnfeat_linear = nn.Linear(frcn_linear_size, __C.HIDDEN_SIZE)

        # if __C.FEATURE['GRID_FEATURE']:
        #     self.gridfeat_linear = nn.Linear(__C.FEATURE['GRIDFEAT_SIZE'], __C.HIDDEN_SIZE)

        self.backbone = BAN(__C)

        # Flatten to vector
        self.attflat_lang = AttFlat(__C)

        # Classification layers
        layers = [
            weight_norm(nn.Linear(__C.FLAT_OUT_SIZE, __C.FLAT_OUT_SIZE), dim=None),
            nn.ReLU(),
            nn.Dropout(__C.CLASSIFER_DROPOUT_R, inplace=True),
            weight_norm(nn.Linear(__C.FLAT_OUT_SIZE, answer_size), dim=None)
        ]
        self.classifer = nn.Sequential(*layers)


    def forward(self, frcn_feat, grid_feat, spat_feat, ques_ix):

        # Pre-process Language Feature
        lang_feat_mask = self.make_mask(ques_ix.unsqueeze(2))
        lang_feat = self.embedding(ques_ix)
        lang_feat, _ = self.rnn(lang_feat)

        # # Pre-process Image Feature
        # frcnfeat_mask = None
        # if self.__C.FEATURE['FRCN_FEATURE']:
        #     frcnfeat_mask = self.make_mask(frcn_feat)
        #     if self.__C.FEATURE['SPATIAL_FEATURE']:
        #         spat_feat = self.spatfeat_linear(spat_feat)
        #         frcn_feat = torch.cat((frcn_feat, spat_feat), dim=-1)
        #     frcn_feat = self.frcnfeat_linear(frcn_feat)

        # gridfeat_mask = None
        # if self.__C.FEATURE['GRID_FEATURE']:
        #     gridfeat_mask = self.make_mask(grid_feat)
        #     grid_feat = self.gridfeat_linear(grid_feat)

        # if self.__C.FEATURE['FRCN_FEATURE']:
        #     if self.__C.FEATURE['GRID_FEATURE']:
        #         img_feat = torch.cat((frcn_feat, grid_feat), dim=1)
        #         img_feat_mask = torch.cat((frcnfeat_mask, gridfeat_mask), dim=-1)
        #     else:
        #         img_feat = frcn_feat
        #         img_feat_mask = frcnfeat_mask
        # else:
        #     img_feat = grid_feat
        #     img_feat_mask = gridfeat_mask


        # Backbone Framework
        lang_feat = self.backbone(
            lang_feat,
            frcn_feat
        )

        # Flatten to vector
        fuse_feat = self.attflat_lang(
            lang_feat,
            lang_feat_mask
        )

        # Classification layers
        proj_feat = self.classifer(fuse_feat)

        return proj_feat


    # Masking the sequence
    def make_mask(self, feature):
        return (torch.sum(
            torch.abs(feature),
            dim=-1
        ) == 0).unsqueeze(1).unsqueeze(2)
