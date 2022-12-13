# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import typing as tp
import torch
from torch import nn

from url_benchmark import utils
from .ddpg import DDPGAgent
from url_benchmark.in_memory_replay_buffer import ReplayBuffer
from typing import Any, Dict, Tuple


class ICM(nn.Module):
    def __init__(self, obs_dim, action_dim, hidden_dim) -> None:
        super().__init__()

        self.forward_net = nn.Sequential(
            nn.Linear(obs_dim + action_dim, hidden_dim), nn.ReLU(),
            nn.Linear(hidden_dim, obs_dim))

        self.backward_net = nn.Sequential(nn.Linear(2 * obs_dim, hidden_dim),
                                          nn.ReLU(),
                                          nn.Linear(hidden_dim, action_dim),
                                          nn.Tanh())

        self.apply(utils.weight_init)

    def forward(self, obs, action, next_obs) -> Tuple[Any, Any]:
        assert obs.shape[0] == next_obs.shape[0]
        assert obs.shape[0] == action.shape[0]

        next_obs_hat = self.forward_net(torch.cat([obs, action], dim=-1))
        action_hat = self.backward_net(torch.cat([obs, next_obs], dim=-1))

        forward_error = torch.norm(next_obs - next_obs_hat,
                                   dim=-1,
                                   p=2,
                                   keepdim=True)
        backward_error = torch.norm(action - action_hat,
                                    dim=-1,
                                    p=2,
                                    keepdim=True)

        return forward_error, backward_error


class ICMAgent(DDPGAgent):
    def __init__(self, icm_scale, update_encoder, **kwargs) -> None:
        super().__init__(**kwargs)
        self.icm_scale = icm_scale
        self.update_encoder = update_encoder

        self.icm = ICM(self.obs_dim, self.action_dim,
                       self.hidden_dim).to(self.device)

        # optimizers
        self.icm_opt = torch.optim.Adam(self.icm.parameters(), lr=self.lr)

        self.icm.train()

    # pylint: disable=unused-argument
    def update_icm(self, obs, action, next_obs, step) -> Dict[str, Any]:
        metrics: tp.Dict[str, float] = {}

        forward_error, backward_error = self.icm(obs, action, next_obs)

        loss = forward_error.mean() + backward_error.mean()

        self.icm_opt.zero_grad(set_to_none=True)
        if self.encoder_opt is not None:
            self.encoder_opt.zero_grad(set_to_none=True)
        loss.backward()
        self.icm_opt.step()
        if self.encoder_opt is not None:
            self.encoder_opt.step()

        if self.use_tb or self.use_wandb:
            metrics['icm_loss'] = loss.item()

        return metrics

    # pylint: disable=unused-argument
    def compute_intr_reward(self, obs, action, next_obs, step) -> Any:
        forward_error, _ = self.icm(obs, action, next_obs)

        reward = forward_error * self.icm_scale
        reward = torch.log(reward + 1.0)
        return reward

    def update(self, replay_loader: ReplayBuffer, step: int) -> tp.Dict[str, float]:
        metrics: tp.Dict[str, float] = {}

        if step % self.update_every_steps != 0:
            return metrics

        batch = replay_loader.sample(self.cfg.batch_size)
        obs, action, extr_reward, discount, next_obs = batch.to(self.device).unpack()

        # augment and encode
        obs = self.aug_and_encode(obs)
        with torch.no_grad():
            next_obs = self.aug_and_encode(next_obs)

        if self.reward_free:
            metrics.update(self.update_icm(obs, action, next_obs, step))

            with torch.no_grad():
                intr_reward = self.compute_intr_reward(obs, action, next_obs,
                                                       step)

            if self.use_tb or self.use_wandb:
                metrics['intr_reward'] = intr_reward.mean().item()
            reward = intr_reward
        else:
            reward = extr_reward

        if self.use_tb or self.use_wandb:
            metrics['extr_reward'] = extr_reward.mean().item()
            metrics['batch_reward'] = reward.mean().item()

        if not self.update_encoder:
            obs = obs.detach()
            next_obs = next_obs.detach()

        # update critic
        metrics.update(
            self.update_critic(obs.detach(), action, reward, discount,
                               next_obs.detach(), step))

        # update actor
        metrics.update(self.update_actor(obs.detach(), step))

        # update critic target
        utils.soft_update_params(self.critic, self.critic_target,
                                 self.critic_target_tau)

        return metrics
