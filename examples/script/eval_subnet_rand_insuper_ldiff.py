import subprocess
from torchpack.utils.logging import logger
import argparse

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--ldiff', type=int)
    parser.add_argument('--gpu', type=int)
    args = parser.parse_args()

    pres = ['python',
            'examples/eval.py',
            'examples/configs/mnist/four0123/eval/tq/all.yml',
            f'--run-dir=runs/mnist.four0123.train.super'
            f'.super4digit_arbitrary_fc1.ldiff.blk8s2_diff{args.ldiff}',
            '--ckpt.name',
            'checkpoints/step-18400.pt',
            '--dataset.split=valid',
            f'--gpu={args.gpu}']
    with open(f'logs/eval_subnet_tq_rand_insuper_ldiff{args.ldiff}.txt',
              'w') as wfid:
        for blk in range(1, 9):
            for rand in range(4):
                exp = f"--model.arch.sample_arch=super4digit_arbitrary_fc1_blk{blk}_rand{rand}"
                logger.info(f"running command {pres + [exp]}")

                subprocess.run(pres + [exp], stderr=wfid)
