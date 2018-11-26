import time
rom datetime import timedelta, datetime
import os
import torch
from tensorboardX import SummaryWriter

from fastNLP.core.batch import Batch
from fastNLP.core.loss import Loss
from fastNLP.core.metrics import Evaluator
from fastNLP.core.optimizer import Optimizer
from fastNLP.core.sampler import RandomSampler
from fastNLP.core.sampler import SequentialSampler
from fastNLP.core.tester import Tester
from fastNLP.core.utils import _check_arg_dict_list
from fastNLP.core.utils import _build_args
from fastNLP.core.utils import _syn_model_data
from fastNLP.core.utils import get_func_signature

class Trainer(object):
    """Main Training Loop

    """
   def __init__(self, train_data, model, n_epochs=3, batch_size=32, print_every=-1,
                 dev_data=None, use_cuda=False, save_path="./save",
                 optimizer=Optimizer("Adam", lr=0.001, weight_decay=0),
                 **kwargs):
        super(Trainer, self).__init__()

        self.train_data = train_data
        self.dev_data = dev_data  # If None, No validation.
        self.model = model
        self.n_epochs = int(n_epochs)
        self.batch_size = int(batch_size)
        self.use_cuda = bool(use_cuda)
        self.save_path = str(save_path)
        self.print_every = int(print_every)

        model_name = model.__class__.__name__
        assert hasattr(self.model, 'get_loss'), "model {} has to have a 'get_loss' function.".format(model_name)
        self.loss_func = self.model.get_loss
        if isinstance(optimizer, torch.optim.Optimizer):
            self.optimizer = optimizer
        else:
            self.optimizer = optimizer.construct_from_pytorch(self.model.parameters())

        assert hasattr(self.model, 'evaluate'), "model {} has to have a 'evaluate' function.".format(model_name)
        self.evaluator = self.model.evaluate

        if self.dev_data is not None:
            valid_args = {"batch_size": self.batch_size, "save_path": self.save_path,
                          "use_cuda": self.use_cuda, "evaluator": self.evaluator}
            self.tester = Tester(**valid_args)

        for k, v in kwargs.items():
            setattr(self, k, v)

        self.tensorboard_path = os.path.join(self.save_path, 'tensorboard_logs')
        if os.path.exists(self.tensorboard_path):
            os.rmdir(self.tensorboard_path)
        self._summary_writer = SummaryWriter(self.tensorboard_path)
        self._graph_summaried = False
        self.step = 0
        self.start_time = None  # start timestamp

        # print(self.__dict__)

    def train(self):
        """Start Training.

        :return:
        """
        if torch.cuda.is_available() and self.use_cuda:
            self.model = self.model.cuda()

        self.mode(self.model, is_test=False)

        start = time.time()
        self.start_time = str(datetime.now().strftime('%Y-%m-%d-%H-%M-%S'))
        print("training epochs started " + self.start_time)

        epoch = 1
        while epoch <= self.n_epochs:

            data_iterator = Batch(self.train_data, batch_size=self.batch_size, sampler=RandomSampler(), as_numpy=False)

            self._train_epoch(data_iterator, self.model, epoch, self.dev_data, start)

            if self.dev_data:
                self.do_validation()
            self.save_model(self.model, 'training_model_' + self.start_time)
            epoch += 1

    def _train_epoch(self, data_iterator, model, epoch, dev_data, start, **kwargs):
        """Training process in one epoch.

            kwargs should contain:
                - n_print: int, print training information every n steps.
                - start: time.time(), the starting time of this step.
                - epoch: int,
        """
        for batch_x, batch_y in data_iterator:
            prediction = self.data_forward(model, batch_x)

            # TODO: refactor self.get_loss
            loss = prediction["loss"] if "loss" in prediction else self.get_loss(prediction, batch_y)
            # acc = self._evaluator([{"predict": prediction["predict"]}], [{"truth": batch_x["truth"]}])

            self.grad_backward(loss)
            self.update()
            self._summary_writer.add_scalar("loss", loss.item(), global_step=self.step)
            for name, param in self.model.named_parameters():
                if param.requires_grad:
                    self._summary_writer.add_scalar(name + "_mean", param.mean(), global_step=self.step)
                    # self._summary_writer.add_scalar(name + "_std", param.std(), global_step=self.step)
                    # self._summary_writer.add_scalar(name + "_grad_sum", param.sum(), global_step=self.step)
           if n_print > 0 and self.step % n_print == 0:
                end = time.time()
                diff = timedelta(seconds=round(end - start))
                print_output = "[epoch: {:>3} step: {:>4}] train loss: {:>4.6} time: {}".format(
                    epoch, self.step, loss.data, diff)
                print(print_output)

            self.step += 1

    def do_validation(self):
        res = self.tester.test(self.model, self.dev_data)
        for name, num in res.items():
            self._summary_writer.add_scalar("valid_{}".format(name), num, global_step=self.step)
        self.save_model(self.model, 'best_model_' + self.start_time)

    def mode(self, model, is_test=False):
        """Train mode or Test mode. This is for PyTorch currently.

        :param model: a PyTorch model
        :param is_test: bool, whether in test mode or not.

        """
        if is_test:
            model.eval()
        else:
            model.train()

    def update(self):
        """Perform weight update on a model.

        """
        self.optimizer.step()

    def data_forward(self, network, x):
        x = _build_args(network.forward, **x)
        y = network(**x)
        if not self._graph_summaried:
            # self._summary_writer.add_graph(network, x, verbose=False)
            self._graph_summaried = True
        return y

    def grad_backward(self, loss):
        """Compute gradient with link rules.

        :param loss: a scalar where back-prop starts

        For PyTorch, just do "loss.backward()"
        """
        self.model.zero_grad()
        loss.backward()

    def get_loss(self, predict, truth):
        """Compute loss given prediction and ground truth.

        :param predict: prediction label vector
        :param truth: ground truth label vector
        :return: a scalar
        """
        assert isinstance(predict, dict) and isinstance(truth, dict)
        args = _build_args(self.loss_func, **predict, **truth)
        return self.loss_func(**args)

    def save_model(self, model, model_name, only_param=False):
        model_name = os.path.join(self.save_path, model_name)
        if only_param:
            torch.save(model.state_dict(), model_name)
        else:
            torch.save(model, model_name)


def best_eval_result(self, metrics):
    """Check if the current epoch yields better validation results.

    :return: bool, True means current results on dev set is the best.
    """
    if isinstance(metrics, tuple):
        loss, metrics = metrics

    if isinstance(metrics, dict):
        if len(metrics) == 1:
            accuracy = list(metrics.values())[0]
        else:
            accuracy = metrics[self.eval_sort_key]
    else:
        accuracy = metrics

    if accuracy > self._best_accuracy:
        self._best_accuracy = accuracy
        return True
    else:
        return False


DEFAULT_CHECK_BATCH_SIZE = 2
DEFAULT_CHECK_NUM_BATCH = 2

IGNORE_CHECK_LEVEL = 0
WARNING_CHECK_LEVEL = 1
STRICT_CHECK_LEVEL = 2

def _check_code(dataset, model, batch_size=DEFAULT_CHECK_BATCH_SIZE, dev_data=None, check_level=WARNING_CHECK_LEVEL):
    # check get_loss 方法
    model_name = model.__class__.__name__
    if not hasattr(model, 'get_loss'):
        raise AttributeError("{} has to have a 'get_loss' function.".format(model_name))

    batch = Batch(dataset=dataset, batch_size=batch_size, sampler=SequentialSampler())
    for batch_count, (batch_x, batch_y) in enumerate(batch):
        if batch_count == 0:
            check_res = _check_arg_dict_list(model.forward, batch_x)
            _info_str = ''
            if len(check_res.missing) > 0:
                if check_level == WARNING_CHECK_LEVEL:
                    for field_name in check_res.missing:
                        if hasattr(dataset, field_name):
                            _info_str += "{} "
                    _info_str += "Missing argument: [{}] needed by '{}.forward' is not presented in the input.\n"
                    _info_str += ""
                    print("")
            if len(check_res.unused) > 0:
                if check_level == WARNING_CHECK_LEVEL:
                    _info_str += ""

        refined_batch_x = _build_args(model.forward, **batch_x)
        output = model(**refined_batch_x)
        signature_str = get_func_signature(model.forward)
        func_signature = '{}.forward(self, {})'.format(model.__class__.__name__, signature_str[1:-1])
        assert isinstance(output, dict), "The return value of {} should be dict.".format(func_signature)

        # loss check
        if batch_count == 0:
           _check_loss(model=model, model_func=model.get_loss, check_level=check_level,
                                 output=output, batch_y=batch_y)
        loss_input = _build_args(model.get_loss, **output, **batch_y)
        loss = model.get_loss(**loss_input)

        # check loss output
        if batch_count == 0:
            if not isinstance(loss, torch.Tensor):
                raise ValueError("The return value of {}.get_loss() should be torch.Tensor, but {} got.".
                                 format(model_name, type(loss)))
            if len(loss.size())!=0:
                raise ValueError("The size of return value of {}.get_loss() is {}, should be torch.size([])".format(
                    model_name, loss.size()
                ))
        loss.backward()
        if batch_count + 1 >= DEFAULT_CHECK_BATCH_SIZE:
            break
    if check_level > IGNORE_CHECK_LEVEL:
        print('Finish checking training process.', flush=True)


    if dev_data is not None:
        if not hasattr(model, 'evaluate'):
            raise AttributeError("{} has to have a 'evaluate' function to do evaluation. Or set"
                                 "dev_data to 'None'."
                                 .format(model_name))
        outputs, truths = defaultdict(list), defaultdict(list)
        dev_batch = Batch(dataset=dataset, batch_size=batch_size, sampler=SequentialSampler())
        with torch.no_grad():
            for batch_count, (batch_x, batch_y) in enumerate(dev_batch):
                _syn_model_data(model, batch_x, batch_y)

                refined_batch_x = _build_args(model.forward, **batch_x)
                output = model(**refined_batch_x)
                for k, v in output.items():
                    outputs[k].append(v)
                for k, v in batch_y.items():
                    truths[k].append(v)
                if batch_count+1>DEFAULT_CHECK_NUM_BATCH:
                    break
            _check_loss(model=model, model_func=model.evaluate, check_level=check_level,
                                 output=outputs, batch_y=truths)
            refined_input = _build_args(model.evaluate, **outputs, **truths)
            metrics = model.evaluate(**refined_input)
            signature_str = get_func_signature(model.evaluate)
            func_signature = '{}.evaluate(self, {})'.format(model.__class__.__name__, signature_str[1:-1])
            assert isinstance(metrics, dict), "The return value of {} should be dict.". \
                format(func_signature)
        if check_level > IGNORE_CHECK_LEVEL:
            print("Finish checking evaluate process.", flush=True)


def _check_forward_error(model, model_func, check_level, batch_x):
    check_res = _check_arg_dict_list(model_func, batch_x)
    _missing = ''
    _unused = ''
    signature_str = get_func_signature(model_func)
    func_signature = '{}.forward(self, {})'.format(model.__class__.__name__, signature_str[1:-1])
    if len(check_res.missing)!=0:
        _missing = "Function {} misses {}, only provided with {}, " \
                   ".\n".format(func_signature, check_res.missing,
                                             list(batch_x.keys()))
    if len(check_res.unused)!=0:
        if len(check_res.unused) > 1:
            _unused = "{} are not used ".format(check_res.unused)
        else:
            _unused = "{} is not used ".format(check_res.unused)
        _unused += "in function {}.\n".format(func_signature)
    if _missing:
        if not _unused and STRICT_CHECK_LEVEL:
            _error_str = "(1).{} (2).{}".format(_missing, _unused)
        else:
            _error_str = _missing
        # TODO 这里可能需要自定义一些Error类型
        raise TypeError(_error_str)
    if _unused:
        if check_level == STRICT_CHECK_LEVEL:
            # TODO 这里可能需要自定义一些Error类型
            raise ValueError(_unused)
        elif check_level == WARNING_CHECK_LEVEL:
            warnings.warn(message=_unused)

def _check_loss(model, model_func, check_level, output, batch_y):
    check_res = _check_arg_dict_list(model_func, [output, batch_y])
    _missing = ''
    _unused = ''
    _duplicated = ''
    signature_str = get_func_signature(model_func)
    model_name = model.__class__.__name__
    model_func_name = model_func.__name__
    func_signature = "{}.{}(self, {})".format(model_name, model_func_name, signature_str[1:-1])
    forward_signature_str = get_func_signature(model.forward)
    forward_func_signature = "{}.forward(self, {})".format(model_name, forward_signature_str[1:-1])
    if len(check_res.missing)>0:
        _missing = "Function {} misses argument {}, only provided with {}(from {}) and " \
                   "{}." \
                   .format(func_signature, check_res.missing,
                            list(output.keys()), model_name,
                           list(batch_y.keys()))
    if len(check_res.unused)>0:
        if len(check_res.unused) > 1:
            _unused = "{} are not used ".format(check_res.unused)
        else:
            _unused = "{} is not used ".format(check_res.unused)
        _unused += "in function {}.\n".format(func_signature)
    if len(check_res.duplicated)>0:
        if len(check_res.duplicated) > 1:
            _duplicated = "Duplicated keys {} are detected when calling function {}. \nDon't set {} as target and output " \
                          "them in {} at the same time.\n".format(check_res.duplicated,
                                                                            func_signature,
                                                                            check_res.duplicated,
                                                                  forward_func_signature)
        else:
            _duplicated = "Duplicated key {} is detected when calling function {}. \nDon't set {} as target and output " \
                          "it in {} at the same time.\n".format(check_res.duplicated,
                                                                func_signature,
                                                                check_res.duplicated,
                                                                forward_func_signature)
    _number_errs = int(len(_missing)!=0) + int(len(_duplicated)!=0) + int(len(_unused)!=0)
    if _number_errs > 0:
        _error_str = ''
        if _number_errs > 1:
            count = 1
            if _missing:
                _error_str += '({}).{}'.format(count, _missing)
                count += 1
            if _duplicated:
                _error_str += '({}).{}'.format(count, _duplicated)
                count += 1
            if _unused and check_level == STRICT_CHECK_LEVEL:
                _error_str += '({}).{}'.format(count, _unused)
        else:
            if _unused:
                if check_level == STRICT_CHECK_LEVEL:
                    # TODO 这里可能需要自定义一些Error类型
                    _error_str = _unused
                elif check_level == WARNING_CHECK_LEVEL:
                    _unused = _unused.strip()
                    warnings.warn(_unused)
            else:
                _error_str = _missing + _duplicated
        if _error_str:
            raise ValueError(_error_str)

def _check_evaluate(model, model_func, check_level, output, batch_y):

    check_res = _check_arg_dict_list(model_func, [output, batch_y])
    _missing = ''
    _unused = ''
    _duplicated = ''
    signature_str = get_func_signature(model_func)
    model_name = model.__class__.__name__
    model_func_name = model_func.__name__
    func_signature = "{}.{}(self, {})".format(model_name, model_func_name, signature_str[1:-1])
    if hasattr(model, 'predict'):
        previous_func = model.predict
        previous_func_name = 'predict'
    else:
        previous_func = model.forward
        previous_func_name = 'forward'
    previous_signature_str = get_func_signature(previous_func)
    previous_func_signature = "{}.{}(self, {})".format(model_name, previous_func_name, previous_signature_str[1:-1])
    if len(check_res.missing)>0:
        _missing = "Function {} misses argument {}, only provided with {}(from {}) and " \
                   "{}." \
                   .format(func_signature, check_res.missing,
                            list(output.keys()), previous_func_signature,
                           list(batch_y.keys()))
    if len(check_res.unused)>0:
        if len(check_res.unused) > 1:
            _unused = "{} are not used ".format(check_res.unused)
        else:
            _unused = "{} is not used ".format(check_res.unused)
        _unused += "in function {}.\n".format(func_signature)
    if len(check_res.duplicated)>0:
        if len(check_res.duplicated) > 1:
            _duplicated = "Duplicated keys {} are detected when calling function {}. \nDon't set {} as target and output " \
                          "them in {} at the same time.\n".format(check_res.duplicated,
                                                                            func_signature,
                                                                            check_res.duplicated,
                                                                  previous_func_signature)
        else:
            _duplicated = "Duplicated key {} is detected when calling function {}. \nDon't set {} as target and output " \
                          "it in {} at the same time.\n".format(check_res.duplicated,
                                                                func_signature,
                                                                check_res.duplicated,
                                                                previous_func_signature)
    _number_errs = int(len(_missing)!=0) + int(len(_duplicated)!=0) + int(len(_unused)!=0)
    if _number_errs > 0:
        _error_str = ''
        if _number_errs > 1:
            count = 1
            if _missing:
                _error_str += '({}).{}'.format(count, _missing)
                count += 1
            if _duplicated:
                _error_str += '({}).{}'.format(count, _duplicated)
                count += 1
            if _unused and check_level == STRICT_CHECK_LEVEL:
                _error_str += '({}).{}'.format(count, _unused)
        else:
            if _unused:
                if check_level == STRICT_CHECK_LEVEL:
                    # TODO 这里可能需要自定义一些Error类型
                    _error_str = _unused
                elif check_level == WARNING_CHECK_LEVEL:
                    _unused = _unused.strip()
                    warnings.warn(_unused)
            else:
                _error_str = _missing + _duplicated
        if _error_str:
            raise ValueError(_error_str)




if __name__ == '__main__':
    import torch
    from torch import nn
    from fastNLP.core.dataset import DataSet
    import numpy as np

    class Model(nn.Module):
        def __init__(self):
            super().__init__()

            self.fc1 = nn.Linear(10, 2)

        def forward(self, words, chars):
            output = {}
            output['prediction'] = torch.randn(3, 4)
            output['words'] = words
            return output

        def get_loss(self, prediction, labels, words):
            return torch.mean(self.fc1.weight)

        def evaluate(self, prediction, labels, demo=2):
            return 0

    model = Model()

    num_samples = 4
    fake_data_dict = {'words': np.random.randint(num_samples, size=(4, 3)), 'chars': np.random.randn(num_samples, 6),
                      'labels': np.random.randint(2, size=(num_samples,))}


    dataset = DataSet(fake_data_dict)
    dataset.set_input(words=True, chars=True)
    dataset.set_target(labels=True)

    # trainer = Trainer(dataset, model)

   _check_code(dataset=dataset, model=model, dev_data=dataset, check_level=1)

    # _check_forward_error(model=model, model_func=model.forward, check_level=1,
    #                     batch_x=fake_data_dict)

    # import inspect
    # print(inspect.getfullargspec(model.forward))

    import numpy as np

    a = [1, 3]
    np.asarray(a)

    import pandas
    df = pandas.DataFrame(fake_data_dict)
    df.infer_objects()


