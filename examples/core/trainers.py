import torch.nn as nn
import torchquantum as tq
import torch.nn.utils.prune

from typing import Any, Callable, Dict
from torchpack.train import Trainer
from torchpack.utils.typing import Optimizer, Scheduler
from torchpack.utils.config import configs
from torchquantum.utils import (get_unitary_loss, legalize_unitary,
                                build_module_op_list)
from torchquantum.super_utils import ArchSampler
from torchquantum.prune_utils import (PhaseL1UnstructuredPruningMethod,
                                      ThresholdScheduler)
from torchpack.utils.logging import logger
from torchpack.callbacks.writers import TFEventWriter


__all__ = ['QTrainer', 'LayerRegressionTrainer', 'SuperQTrainer',
           'PruningTrainer']


class LayerRegressionTrainer(Trainer):
    def __init__(self, *, model: nn.Module, criterion: Callable,
                 optimizer: Optimizer, scheduler: Scheduler) -> None:
        self.model = model
        self.criterion = criterion
        self.optimizer = optimizer
        self.scheduler = scheduler

    def _before_epoch(self) -> None:
        self.model.train()

    def _run_step(self, feed_dict: Dict[str, Any]) -> Dict[str, Any]:
        if configs.run.device == 'gpu':
            inputs = feed_dict['input'].cuda(non_blocking=True)
            targets = feed_dict['output'].cuda(non_blocking=True)
        else:
            inputs = feed_dict['input']
            targets = feed_dict['output']

        outputs = self.model(inputs)
        loss = self.criterion(outputs, targets)

        if loss.requires_grad:
            self.summary.add_scalar('loss', loss.item())

            self.optimizer.zero_grad()
            loss.backward()
            self.optimizer.step()

        return {'outputs': outputs, 'targets': targets}

    def _after_epoch(self) -> None:
        self.model.eval()
        self.scheduler.step()

    def _state_dict(self) -> Dict[str, Any]:
        state_dict = dict()
        state_dict['model'] = self.model.state_dict()
        state_dict['optimizer'] = self.optimizer.state_dict()
        state_dict['scheduler'] = self.scheduler.state_dict()
        return state_dict

    def _load_state_dict(self, state_dict: Dict[str, Any]) -> None:
        self.model.load_state_dict(state_dict['model'])
        self.optimizer.load_state_dict(state_dict['optimizer'])
        self.scheduler.load_state_dict(state_dict['scheduler'])


class QTrainer(Trainer):
    def __init__(self, *, model: nn.Module, criterion: Callable,
                 optimizer: Optimizer, scheduler: Scheduler) -> None:
        self.model = model
        self.legalized_model = None
        self.criterion = criterion
        self.optimizer = optimizer
        self.scheduler = scheduler
        self.solution = None
        self.score = None

    def _before_epoch(self) -> None:
        self.model.train()

    def run_step(self, feed_dict: Dict[str, Any], legalize=False) -> Dict[
            str, Any]:
        output_dict = self._run_step(feed_dict, legalize=legalize)
        return output_dict

    def _run_step(self, feed_dict: Dict[str, Any], legalize=False) -> Dict[
            str, Any]:
        if configs.run.device == 'gpu':
            inputs = feed_dict[configs.dataset.input_name].cuda(
                non_blocking=True)
            targets = feed_dict[configs.dataset.target_name].cuda(
                non_blocking=True)
        else:
            inputs = feed_dict[configs.dataset.input_name]
            targets = feed_dict[configs.dataset.target_name]
        if legalize:
            outputs = self.legalized_model(inputs)
        else:
            outputs = self.model(inputs)
        loss = self.criterion(outputs, targets)
        nll_loss = loss.item()
        unitary_loss = 0

        if configs.regularization.unitary_loss:
            unitary_loss = get_unitary_loss(self.model)
            if configs.regularization.unitary_loss_lambda_trainable:
                loss += self.model.unitary_loss_lambda[0] * unitary_loss
            else:
                loss += configs.regularization.unitary_loss_lambda * \
                        unitary_loss

        if loss.requires_grad:
            for k, group in enumerate(self.optimizer.param_groups):
                self.summary.add_scalar(f'lr/lr_group{k}', group['lr'])
            self.summary.add_scalar('loss', loss.item())
            self.summary.add_scalar('nll_loss', nll_loss)
            if getattr(self.model, 'sample_arch', None) is not None:
                for writer in self.summary.writers:
                    if isinstance(writer, TFEventWriter):
                        writer.writer.add_text(
                            'sample_arch', str(self.model.sample_arch),
                            self.global_step)

            if configs.regularization.unitary_loss:
                if configs.regularization.unitary_loss_lambda_trainable:
                    self.summary.add_scalar(
                        'u_loss_lambda',
                        self.model.unitary_loss_lambda.item())
                else:
                    self.summary.add_scalar(
                        'u_loss_lambda',
                        configs.regularization.unitary_loss_lambda)
                self.summary.add_scalar('u_loss', unitary_loss.item())

            self.optimizer.zero_grad()
            loss.backward()
            self.optimizer.step()

        return {'outputs': outputs, 'targets': targets}

    def _after_epoch(self) -> None:
        self.model.eval()
        self.scheduler.step()
        if configs.legalization.legalize:
            if self.epoch_num % configs.legalization.epoch_interval == 0:
                legalize_unitary(self.model)

    def _after_step(self, output_dict) -> None:
        if configs.legalization.legalize:
            if self.global_step % configs.legalization.step_interval == 0:
                legalize_unitary(self.model)

    def _state_dict(self) -> Dict[str, Any]:
        state_dict = dict()
        # need to store model arch because of randomness of random layers
        state_dict['model_arch'] = self.model
        state_dict['model'] = self.model.state_dict()
        state_dict['optimizer'] = self.optimizer.state_dict()
        state_dict['scheduler'] = self.scheduler.state_dict()
        if getattr(self.model, 'sample_arch', None) is not None:
            state_dict['sample_arch'] = self.model.sample_arch

        if self.solution is not None:
            state_dict['solution'] = self.solution
            state_dict['score'] = self.score

        try:
            state_dict['v_c_reg_mapping'] = self.model.measure.v_c_reg_mapping
        except AttributeError:
            logger.warning(f"No v_c_reg_mapping found, will not save it.")
        return state_dict

    def _load_state_dict(self, state_dict: Dict[str, Any]) -> None:
        # self.model.load_state_dict(state_dict['model'])
        self.optimizer.load_state_dict(state_dict['optimizer'])
        self.scheduler.load_state_dict(state_dict['scheduler'])


class SuperQTrainer(Trainer):
    def __init__(self, *, model: nn.Module, criterion: Callable,
                 optimizer: Optimizer, scheduler: Scheduler) -> None:
        self.model = model
        self.legalized_model = None
        self.criterion = criterion
        self.optimizer = optimizer
        self.scheduler = scheduler
        self.sample_arch = None
        self.arch_sampler = ArchSampler(
            model=model,
            strategy=configs.model.sampler.strategy.dict(),
            n_layers_per_block=configs.model.arch.n_layers_per_block
        )

    def _before_epoch(self) -> None:
        self.model.train()
        self.arch_sampler.set_total_steps(self.steps_per_epoch *
                                          self.num_epochs)

    def _before_step(self, feed_dict: Dict[str, Any]) -> None:
        self.sample_arch = self.arch_sampler.get_uniform_sample_arch()
        self.model.set_sample_arch(self.sample_arch)

    def run_step(self, feed_dict: Dict[str, Any], legalize=False) -> Dict[
            str, Any]:
        output_dict = self._run_step(feed_dict, legalize=legalize)
        return output_dict

    def _run_step(self, feed_dict: Dict[str, Any], legalize=False) -> Dict[
            str, Any]:
        if configs.run.device == 'gpu':
            inputs = feed_dict[configs.dataset.input_name].cuda(
                non_blocking=True)
            targets = feed_dict[configs.dataset.target_name].cuda(
                non_blocking=True)
        else:
            inputs = feed_dict[configs.dataset.input_name]
            targets = feed_dict[configs.dataset.target_name]
        if legalize:
            outputs = self.legalized_model(inputs)
        else:
            outputs = self.model(inputs)
        loss = self.criterion(outputs, targets)
        # nll_loss = loss.item()
        unitary_loss = 0

        if configs.regularization.unitary_loss:
            unitary_loss = get_unitary_loss(self.model)
            if configs.regularization.unitary_loss_lambda_trainable:
                loss += self.model.unitary_loss_lambda[0] * unitary_loss
            else:
                loss += configs.regularization.unitary_loss_lambda * \
                        unitary_loss

        if loss.requires_grad:
            # during training
            for k, group in enumerate(self.optimizer.param_groups):
                self.summary.add_scalar(f'lr/lr_group{k}', group['lr'])
            self.summary.add_scalar('loss', loss.item())
            # self.summary.add_scalar('nll_loss', nll_loss)
            self.summary.add_scalar('sta',
                                    self.arch_sampler.current_stage)
            self.summary.add_scalar('chk',
                                    self.arch_sampler.current_chunk)
            self.summary.add_scalar('n_ops',
                                    self.arch_sampler.sample_n_ops)
            if getattr(self.model, 'sample_arch', None) is not None:
                for writer in self.summary.writers:
                    if isinstance(writer, TFEventWriter):
                        writer.writer.add_text(
                            'sample_arch', str(self.model.sample_arch),
                            self.global_step)

            if configs.regularization.unitary_loss:
                if configs.regularization.unitary_loss_lambda_trainable:
                    self.summary.add_scalar(
                        'u_loss_lambda',
                        self.model.unitary_loss_lambda.item())
                else:
                    self.summary.add_scalar(
                        'u_loss_lambda',
                        configs.regularization.unitary_loss_lambda)
                self.summary.add_scalar('u_loss', unitary_loss.item())

            self.optimizer.zero_grad()
            loss.backward()
            self.optimizer.step()

        return {'outputs': outputs, 'targets': targets}

    def _after_epoch(self) -> None:
        self.model.eval()
        self.scheduler.step()
        if configs.legalization.legalize:
            if self.epoch_num % configs.legalization.epoch_interval == 0:
                legalize_unitary(self.model)

    def _after_step(self, output_dict) -> None:
        if configs.legalization.legalize:
            if self.global_step % configs.legalization.step_interval == 0:
                legalize_unitary(self.model)

    def _state_dict(self) -> Dict[str, Any]:
        state_dict = dict()
        # need to store model arch because of randomness of random layers
        state_dict['model_arch'] = self.model
        state_dict['model'] = self.model.state_dict()
        state_dict['optimizer'] = self.optimizer.state_dict()
        state_dict['scheduler'] = self.scheduler.state_dict()
        if getattr(self.model, 'sample_arch', None) is not None:
            state_dict['sample_arch'] = self.model.sample_arch
        try:
            state_dict['v_c_reg_mapping'] = self.model.measure.v_c_reg_mapping
        except AttributeError:
            logger.warning(f"No v_c_reg_mapping found, will not save it.")
        return state_dict

    def _load_state_dict(self, state_dict: Dict[str, Any]) -> None:
        # self.model.load_state_dict(state_dict['model'], strict=False)
        self.optimizer.load_state_dict(state_dict['optimizer'])
        self.scheduler.load_state_dict(state_dict['scheduler'])


class PruningTrainer(Trainer):
    """
    Perform pruning-aware training
    """
    def __init__(self, *, model: nn.Module, criterion: Callable,
                 optimizer: Optimizer, scheduler: Scheduler) -> None:
        self.model = model
        self.legalized_model = None
        self.criterion = criterion
        self.optimizer = optimizer
        self.scheduler = scheduler
        self.solution = None
        self.score = None

        self._parameters_to_prune = None
        self._target_pruning_amount = None
        self._init_pruning_amount = None
        self.prune_amount_scheduler = None
        self.prune_amount = None

        self.init_pruning()

    @staticmethod
    def extract_prunable_parameters(model: nn.Module) -> tuple:
        _parameters_to_prune = [
            (module, "params")
            for _, module in model.named_modules() if isinstance(module,
                                                                 tq.Operator)
            and module.params is not None]
        return _parameters_to_prune

    def init_pruning(self) -> None:
        """
        Initialize pruning procedure
        """
        self._parameters_to_prune = self.extract_prunable_parameters(
            self.model)
        self._target_pruning_amount = configs.prune.target_pruning_amount
        self._init_pruning_amount = configs.prune.init_pruning_amount
        self.prune_amount_scheduler = ThresholdScheduler(
            configs.prune.start_epoch, configs.prune.end_epoch,
            self._init_pruning_amount,
            self._target_pruning_amount)
        self.prune_amount = self._init_pruning_amount

    def _remove_pruning(self):
        for module, name in self._parameters_to_prune:
            nn.utils.prune.remove(module, name)

    def _prune_model(self, prune_amount) -> None:
        """
        Perform global threshold/percentage pruning on the quantum model.
        This function just performs pruning re-parametrization, i.e.,
        record weight_orig and generate weight_mask
        """
        # first clear current pruning container, since we do not want cascaded
        # pruning methods
        # remove operation will make pruning permanent
        if self.epoch_num > 1:
            self._remove_pruning()
        # perform global phase pruning based on the given pruning amount
        nn.utils.prune.global_unstructured(
            self._parameters_to_prune,
            pruning_method=PhaseL1UnstructuredPruningMethod,
            amount=prune_amount,
        )
        self.summary.add_scalar('prune_amount', prune_amount)

    def _before_epoch(self) -> None:
        self.model.train()

    def run_step(self, feed_dict: Dict[str, Any], legalize=False) -> Dict[
            str, Any]:
        output_dict = self._run_step(feed_dict, legalize=legalize)
        return output_dict

    def _run_step(self, feed_dict: Dict[str, Any], legalize=False) -> Dict[
            str, Any]:
        if configs.run.device == 'gpu':
            inputs = feed_dict[configs.dataset.input_name].cuda(
                non_blocking=True)
            targets = feed_dict[configs.dataset.target_name].cuda(
                non_blocking=True)
        else:
            inputs = feed_dict[configs.dataset.input_name]
            targets = feed_dict[configs.dataset.target_name]
        if legalize:
            outputs = self.legalized_model(inputs)
        else:
            outputs = self.model(inputs)
        loss = self.criterion(outputs, targets)
        nll_loss = loss.item()
        unitary_loss = 0

        if configs.regularization.unitary_loss:
            unitary_loss = get_unitary_loss(self.model)
            if configs.regularization.unitary_loss_lambda_trainable:
                loss += self.model.unitary_loss_lambda[0] * unitary_loss
            else:
                loss += configs.regularization.unitary_loss_lambda * \
                        unitary_loss

        if loss.requires_grad:
            for k, group in enumerate(self.optimizer.param_groups):
                self.summary.add_scalar(f'lr/lr_group{k}', group['lr'])

            self.summary.add_scalar('loss', loss.item())
            self.summary.add_scalar('nll_loss', nll_loss)
            if getattr(self.model, 'sample_arch', None) is not None:
                for writer in self.summary.writers:
                    if isinstance(writer, TFEventWriter):
                        writer.writer.add_text(
                            'sample_arch', str(self.model.sample_arch),
                            self.global_step)

            if configs.regularization.unitary_loss:
                if configs.regularization.unitary_loss_lambda_trainable:
                    self.summary.add_scalar(
                        'u_loss_lambda',
                        self.model.unitary_loss_lambda.item())
                else:
                    self.summary.add_scalar(
                        'u_loss_lambda',
                        configs.regularization.unitary_loss_lambda)
                self.summary.add_scalar('u_loss', unitary_loss.item())

            self.optimizer.zero_grad()
            loss.backward()
            self.optimizer.step()

        return {'outputs': outputs, 'targets': targets}

    def _after_epoch(self) -> None:
        self.model.eval()
        self.scheduler.step()
        if configs.legalization.legalize:
            if self.epoch_num % configs.legalization.epoch_interval == 0:
                legalize_unitary(self.model)
        # update pruning amount using the scheduler
        self.prune_amount = self.prune_amount_scheduler.step()
        # prune the model
        self._prune_model(self.prune_amount)
        # commit pruned parameters after training
        if self.epoch_num == self.num_epochs:
            self._remove_pruning()

    def _after_step(self, output_dict) -> None:
        if configs.legalization.legalize:
            if self.global_step % configs.legalization.step_interval == 0:
                legalize_unitary(self.model)

    def _state_dict(self) -> Dict[str, Any]:
        state_dict = dict()
        # need to store model arch because of randomness of random layers
        state_dict['model_arch'] = self.model
        state_dict['model'] = self.model.state_dict()
        state_dict['optimizer'] = self.optimizer.state_dict()
        state_dict['scheduler'] = self.scheduler.state_dict()
        if getattr(self.model, 'sample_arch', None) is not None:
            state_dict['sample_arch'] = self.model.sample_arch
        try:
            state_dict['q_layer_op_list'] = build_module_op_list(
                self.model.q_layer)
            state_dict['encoder_func_list'] = self.model.encoder.func_list
        except AttributeError:
            logger.warning(f"No q_layer_op_list or encoder_func_list found, "
                           f"will not save them")

        if self.solution is not None:
            state_dict['solution'] = self.solution
            state_dict['score'] = self.score

        try:
            state_dict['v_c_reg_mapping'] = self.model.measure.v_c_reg_mapping
        except AttributeError:
            logger.warning(f"No v_c_reg_mapping found, will not save it.")
        return state_dict

    def _load_state_dict(self, state_dict: Dict[str, Any]) -> None:
        # self.model.load_state_dict(state_dict['model'])
        self.optimizer.load_state_dict(state_dict['optimizer'])
        self.scheduler.load_state_dict(state_dict['scheduler'])
