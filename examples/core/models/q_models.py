import torchquantum as tq
import torchquantum.functional as tqf
import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np

from torchpack.utils.config import configs

__all__ = ['QuanvModel0', 'QuanvModel1', 'QFCModel0',
           'model_dict']


class Quanv0(tq.QuantumModule):
    def __init__(self, n_wires):
        super().__init__()
        self.n_wires = n_wires
        self.random_layer = tq.RandomLayer(n_ops=200, wires=list(range(
            self.n_wires)))

    @tq.static_support
    def forward(self, q_device: tq.QuantumDevice):
        self.q_device = q_device
        self.random_layer(self.q_device)


class QuanvModel0(tq.QuantumModule):
    """
    Convolution with quantum filter
    """
    def __init__(self):
        super().__init__()
        self.q_device = tq.QuantumDevice(n_wires=9)
        self.q_device1 = tq.QuantumDevice(n_wires=12)
        self.measure = tq.MeasureAll(obs=tq.PauliZ)
        self.wires_per_block = 5

        self.encoder0 = tq.PhaseEncoder(func=tqf.rx)
        self.encoder0.static_on(wires_per_block=self.wires_per_block)
        self.quanv0 = tq.QuantumModuleList()
        for k in range(3):
            self.quanv0.append(Quanv0(n_wires=9))
            self.quanv0[k].static_on(wires_per_block=self.wires_per_block)

        self.quanv1 = tq.QuantumModuleList()
        self.encoder1 = tq.PhaseEncoder(func=tqf.rx)
        self.encoder1.static_on(wires_per_block=self.wires_per_block)
        for k in range(10):
            self.quanv1.append(Quanv0(n_wires=12))
            self.quanv1[k].static_on(wires_per_block=self.wires_per_block)

    def forward(self, x):
        bsz = x.shape[0]
        x = F.unfold(x, kernel_size=3, stride=2)
        x = x.permute(0, 2, 1)
        x = x.reshape(-1, x.shape[-1])

        quanv0_results = []
        for k in range(3):
            self.encoder0(self.q_device, x)
            self.quanv0[k](self.q_device)
            x = self.measure(self.q_device)
            quanv0_results.append(x.sum(-1).view(bsz, 13, 13))
        x = torch.stack(quanv0_results, dim=1)

        x = F.unfold(x, kernel_size=2, stride=2)
        x = x.permute(0, 2, 1)
        x = x.reshape(-1, x.shape[-1])

        quanv1_results = []
        for k in range(10):
            self.encoder1(self.q_device1, x)
            self.quanv1[k](self.q_device1)
            x = self.measure(self.q_device1)
            quanv1_results.append(x.sum(-1).view(bsz, 6, 6))
        x = torch.stack(quanv1_results, dim=1)

        x = F.avg_pool2d(x, kernel_size=6)
        x = F.log_softmax(x, dim=1)
        x = x.squeeze()

        return x


class QuanvModel1(tq.QuantumModule):
    """
    Convolution with quantum filter
    """
    def __init__(self):
        super().__init__()
        self.q_device = tq.QuantumDevice(n_wires=4)
        self.measure = tq.MeasureAll(obs=tq.PauliZ)
        self.wires_per_block = 4
        self.n_quanv = 3

        self.encoder0 = tq.PhaseEncoder(func=tqf.rx)
        # self.encoder0.static_on(wires_per_block=self.wires_per_block)
        self.quanv0_all = tq.QuantumModuleList()
        for k in range(self.n_quanv):
            self.quanv0_all.append(Quanv0(n_wires=4))
            # self.quanv0[k].static_on(wires_per_block=self.wires_per_block)

        self.quanv1_all = tq.QuantumModuleList()
        # self.encoder1.static_on(wires_per_block=self.wires_per_block)
        for k in range(10):
            self.quanv1_all.append(Quanv0(n_wires=4))
            # self.quanv1[k].static_on(wires_per_block=self.wires_per_block)

    def forward(self, x):
        bsz = x.shape[0]
        x = F.avg_pool2d(x, 6)

        x = F.unfold(x, kernel_size=2, stride=1)
        x = x.permute(0, 2, 1)
        x = x.reshape(-1, x.shape[-1])
        x = F.tanh(x) * np.pi

        for k in range(self.n_quanv):
            self.encoder0(self.q_device, x)
            self.quanv0_all[k](self.q_device)
            x = self.measure(self.q_device)
            x = x * np.pi

        # x = x.view(bsz, 3, 3, 4).permute(0, 3, 1, 2)

        # for k in range(3):
        #     self.encoder0(self.q_device, x)
        #     self.quanv0[k](self.q_device)
        #     x = self.measure(self.q_device)
        #     quanv0_results.append(x.sum(-1).view(bsz, 13, 13))
        # x = torch.stack(quanv0_results, dim=1)

        # x = F.unfold(x, kernel_size=2, stride=2)
        # x = x.permute(0, 2, 1)
        # x = x.reshape(-1, x.shape[-1])

        quanv1_results = []
        for k in range(10):
            self.encoder0(self.q_device, x)
            self.quanv1_all[k](self.q_device)
            x = self.measure(self.q_device)
            quanv1_results.append(x.sum(-1).view(bsz, 3, 3))
        x = torch.stack(quanv1_results, dim=1)

        x = F.avg_pool2d(x, kernel_size=3)
        x = F.log_softmax(x, dim=1)
        x = x.squeeze()

        return x


class QFCModel0(tq.QuantumModule):
    def __init__(self):
        super().__init__()
        self.q_device = tq.QuantumDevice(n_wires=4)
        self.encoder = tq.StateEncoder()
        self.trainable_u = tq.TrainableUnitary(has_params=True,
                                               trainable=True,
                                               n_wires=4)

    def forward(self, x):
        bsz = x.shape[0]
        x = F.avg_pool2d(x, 6).view(bsz, 16)

        self.encoder(self.q_device, x)
        self.trainable_u(self.q_device, wires=[0, 1, 2, 3])

        x = self.q_device.states.view(bsz, 16)[:, :len(
            configs.dataset.digits_of_interest)].abs()

        x = F.log_softmax(x, dim=1)

        return x


class QFCModel1(tq.QuantumModule):
    def __init__(self):
        super().__init__()
        self.q_device = tq.QuantumDevice(n_wires=4)
        self.encoder = tq.StateEncoder()
        self.trainable_u = tq.TrainableUnitary(has_params=True,
                                               trainable=True,
                                               n_wires=4)
        self.measure = tq.MeasureAll(tq.PauliZ)

    def forward(self, x):
        bsz = x.shape[0]
        x = F.avg_pool2d(x, 6).view(bsz, 16)

        self.encoder(self.q_device, x)
        self.trainable_u(self.q_device, wires=[0, 1, 2, 3])

        x = self.measure(self.q_device).view(bsz, 4)[:, :len(
            configs.dataset.digits_of_interest)]

        x = F.log_softmax(x, dim=1)

        return x


class QFCModel2(tq.QuantumModule):
    def __init__(self):
        super().__init__()
        self.n_wires = 16
        self.q_device = tq.QuantumDevice(n_wires=self.n_wires)
        self.encoder = tq.PhaseEncoder(tqf.rx)
        self.trainable_u = tq.TrainableUnitary(has_params=True,
                                               trainable=True,
                                               n_wires=self.n_wires)
        self.measure = tq.MeasureAll(tq.PauliZ)

    def forward(self, x):
        bsz = x.shape[0]
        x = F.avg_pool2d(x, 6).view(bsz, 16)

        self.encoder(self.q_device, x)
        self.trainable_u(self.q_device, wires=list(range(self.n_wires)))
        x = self.q_device.states.view(bsz, 16)[:, :len(
            configs.dataset.digits_of_interest)].abs()
        # x = self.measure(self.q_device).view(bsz, self.n_wires)[:, :len(
        #     configs.dataset.digits_of_interest)]

        x = F.log_softmax(x, dim=1)

        return x


class QFCModel3(tq.QuantumModule):
    def __init__(self):
        super().__init__()
        self.q_device = tq.QuantumDevice(n_wires=10)
        self.encoder = tq.StateEncoder()
        self.trainable_u = tq.TrainableUnitary(has_params=True,
                                               trainable=True,
                                               n_wires=10)
        self.trainable_u1 = tq.TrainableUnitary(has_params=True,
                                                trainable=True,
                                                n_wires=10)
        if configs.regularization.unitary_loss_lambda_trainable:
            unitary_loss_lambda = nn.Parameter(
                torch.ones(1) * configs.regularization.unitary_loss_lambda)
            self.register_parameter('unitary_loss_lambda', unitary_loss_lambda)

    def forward(self, x):
        bsz = x.shape[0]
        x = x.view(bsz, 784)

        self.encoder(self.q_device, x)
        self.trainable_u(self.q_device, wires=list(range(10)))
        self.trainable_u1(self.q_device, wires=list(range(10)))

        x = self.q_device.states.view(bsz, 1024)[:, :10].abs()

        x = F.log_softmax(x, dim=1)

        return x


class QuanvModel2(tq.QuantumModule):
    """
    Convolution with quantum filter
    """
    def __init__(self):
        super().__init__()
        self.q_device = tq.QuantumDevice(n_wires=4)
        self.measure = tq.MeasureAll(obs=tq.PauliZ)
        self.encoder = tq.PhaseEncoder(func=tqf.rx)

        self.quanv0 = tq.TrainableUnitary(has_params=True,
                                          trainable=True,
                                          n_wires=4)

        self.quanv1 = tq.TrainableUnitary(has_params=True,
                                          trainable=True,
                                          n_wires=4)

        self.quanv2 = tq.QuantumModuleList()
        for k in range(2):
            self.quanv2.append(
                tq.TrainableUnitary(has_params=True,
                                    trainable=True,
                                    n_wires=4)
            )

    def forward(self, x):
        bsz = x.shape[0]
        x = F.avg_pool2d(x, 6)

        x = F.unfold(x, kernel_size=2, stride=1)
        x = x.permute(0, 2, 1)
        x = x.reshape(-1, x.shape[-1])
        x = F.tanh(x) * np.pi

        self.encoder(self.q_device, x)
        self.quanv0(self.q_device, wires=[0, 1, 2, 3])
        x = self.measure(self.q_device)
        x = x * np.pi

        self.encoder(self.q_device, x)
        self.quanv1(self.q_device, wires=[0, 1, 2, 3])
        x = self.measure(self.q_device)
        x = x * np.pi

        x = x.view(bsz, 3, 3, 4)
        x = x.permute(0, 3, 1, 2)

        quanv2_results = []
        for k in range(2):
            tmp = x[:, k, :, :].unsqueeze(1)
            tmp = F.unfold(tmp, kernel_size=2, stride=1)  # bsz, 4, 4
            tmp = tmp.permute(0, 2, 1)
            tmp = tmp.reshape(-1, tmp.shape[-1])
            self.encoder(self.q_device, tmp)
            self.quanv2[k](self.q_device, wires=[0, 1, 2, 3])
            tmp = self.measure(self.q_device)
            quanv2_results.append(tmp.sum(-1).view(bsz, 2, 2))
        x = torch.stack(quanv2_results, dim=1)

        x = F.avg_pool2d(x, kernel_size=2
                         )[:, :len(configs.dataset.digits_of_interest)]
        x = F.log_softmax(x, dim=1)
        x = x.squeeze()

        return x


class QuanvModel3(tq.QuantumModule):
    """
    Convolution with quantum filter
    """
    def __init__(self):
        super().__init__()
        self.q_device = tq.QuantumDevice(n_wires=4)
        self.n_wires = 4
        self.measure = tq.MeasureAll(obs=tq.PauliZ)
        self.encoder = tq.PhaseEncoder(func=tqf.rx)

        self.quanv0 = tq.RandomLayer(n_ops=200, wires=list(range(
            self.n_wires)))
        self.quanv0.static_on(wires_per_block=2)

        self.quanv1 = tq.RandomLayer(n_ops=200, wires=list(range(
            self.n_wires)))

        self.quanv2 = tq.QuantumModuleList()
        for k in range(2):
            self.quanv2.append(
                tq.RandomLayer(n_ops=200, wires=list(range(
                    self.n_wires)))
            )

    def forward(self, x):
        bsz = x.shape[0]
        x = F.avg_pool2d(x, 6)

        x = F.unfold(x, kernel_size=2, stride=1)
        x = x.permute(0, 2, 1)
        x = x.reshape(-1, x.shape[-1])
        x = F.tanh(x) * np.pi

        self.encoder(self.q_device, x)
        self.quanv0(self.q_device)
        x = self.measure(self.q_device)
        x = x * np.pi

        self.encoder(self.q_device, x)
        self.quanv1(self.q_device)
        x = self.measure(self.q_device)
        x = x * np.pi

        x = x.view(bsz, 3, 3, 4)
        x = x.permute(0, 3, 1, 2)

        quanv2_results = []
        for k in range(2):
            tmp = x[:, k, :, :].unsqueeze(1)
            tmp = F.unfold(tmp, kernel_size=2, stride=1)  # bsz, 4, 4
            tmp = tmp.permute(0, 2, 1)
            tmp = tmp.reshape(-1, tmp.shape[-1])
            self.encoder(self.q_device, tmp)
            self.quanv2[k](self.q_device)
            tmp = self.measure(self.q_device)
            quanv2_results.append(tmp.sum(-1).view(bsz, 2, 2))
        x = torch.stack(quanv2_results, dim=1)

        x = F.avg_pool2d(x, kernel_size=2
                         )[:, :len(configs.dataset.digits_of_interest)]
        x = F.log_softmax(x, dim=1)
        x = x.squeeze()

        return x


# Qubitization according to the Quantum Singular Value Transformation paper
class QSVT0(tq.QuantumModule):
    def __init__(self,
                 n_wires=8,
                 n_xcnot_wires=8,
                 depth=16
                 ):
        super().__init__()
        self.n_wires = n_wires
        self.q_device = tq.QuantumDevice(n_wires=n_wires)
        self.depth = depth
        self.u = tq.TrainableUnitary(has_params=True,
                                     trainable=True,
                                     n_wires=self.n_wires - 1)

        self.rzs = tq.QuantumModuleList()
        for k in range(self.depth):
            self.rzs.append(tq.RZ())
        self.xcnot = tq.MultiXCNOT(n_wires=n_xcnot_wires)
        self.xcnot_wires = list(range(1, n_xcnot_wires)) + [0]
        self.measure = tq.MeasureAll(tq.PauliZ)

    def forward(self, x):
        bsz = x.shape[0]
        x = F.avg_pool2d(x, 6)
        x = x.view(bsz, self.depth)
        x = F.tanh(x) * np.pi
        self.q_device.reset_states(bsz=bsz)

        # prepare for the |+> state
        tqf.h(self.q_device, wires=0)

        for k in range(self.depth):
            self.xcnot(self.q_device, wires=self.xcnot_wires)
            self.rzs[k](self.q_device, wires=0, params=x[:, k])
            self.xcnot(self.q_device, wires=self.xcnot_wires)
            self.u(self.q_device, wires=list(range(1, self.n_wires)),
                   inverse=(k % 2 == 1))

        x = self.measure(self.q_device)[:, :len(
            configs.dataset.digits_of_interest)]
        x = F.log_softmax(x, dim=1)
        x = x.squeeze()

        return x


model_dict = {
    'q_quanv0': QuanvModel0,
    'q_quanv1': QuanvModel1,
    'q_quanv2': QuanvModel2,
    'q_quanv3': QuanvModel3,
    'q_fc0': QFCModel0,
    'q_fc1': QFCModel1,
    'q_fc2': QFCModel2,
    'q_fc3': QFCModel3,
    'q_qsvt0': QSVT0,
}