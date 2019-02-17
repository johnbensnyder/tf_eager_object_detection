import tensorflow as tf
import os
import time
from object_detection.model.extractor.feature_extractor import Vgg16Extractor
from object_detection.model.extractor.resnet import ResNetExtractor
from object_detection.dataset.pascal_tf_dataset_generator import get_dataset
from object_detection.model.faster_rcnn import BaseFasterRcnnModel, FasterRcnnTrainingModel, RpnTrainingModel, \
    RoiTrainingModel, post_ops_prediction
from object_detection.config.faster_rcnn_config import CONFIG
from object_detection.utils.pascal_voc_map_utils import eval_detection_voc
from object_detection.utils.visual_utils import show_one_image
from tensorflow.contrib.summary import summary
from tensorflow.contrib.eager.python import saver as eager_saver
from tqdm import tqdm
from tensorflow.python.platform import tf_logging

os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"  # see issue #152
os.environ["CUDA_VISIBLE_DEVICES"] = "1"

tf.enable_eager_execution()


def apply_gradients(model, optimizer, gradients):
    # for grad, var in zip(gradients, model.variables):
    #     if grad is not None:
    #         tf_logging.info(var.name, var.trainable, tf.reduce_min(grad).numpy(), tf.reduce_max(grad).numpy())

    optimizer.apply_gradients(zip(gradients, model.variables),
                              global_step=tf.train.get_or_create_global_step())


def compute_gradients(model, loss, tape):
    return tape.gradient(loss, model.variables)


def train_step(model, loss, tape, optimizer):
    apply_gradients(model, optimizer, compute_gradients(model, loss, tape))


def _get_resnet101_faster_rcnn_model():
    base_model = BaseFasterRcnnModel(
        ratios=CONFIG['ratios'],
        scales=CONFIG['scales'],
        extractor=ResNetExtractor(50),
        extractor_stride=CONFIG['extractor_stride'],

        weight_decay=CONFIG['weight_decay'],
        rpn_proposal_num_pre_nms_train=CONFIG['rpn_proposal_train_pre_nms_sample_number'],
        rpn_proposal_num_post_nms_train=CONFIG['rpn_proposal_train_after_nms_sample_number'],
        rpn_proposal_num_pre_nms_test=CONFIG['rpn_proposal_test_pre_nms_sample_number'],
        rpn_proposal_num_post_nms_test=CONFIG['rpn_proposal_test_after_nms_sample_number'],
        rpn_proposal_nms_iou_threshold=CONFIG['rpn_proposal_nms_iou_threshold'],

        roi_pool_size=CONFIG['roi_pooling_size'],
        num_classes=CONFIG['num_classes'],
        roi_head_keep_dropout_rate=CONFIG['roi_head_keep_dropout_rate'],
    )
    training_model = FasterRcnnTrainingModel(
        rpn_training_model=RpnTrainingModel(
            cls_loss_weight=CONFIG['rpn_cls_loss_weight'],
            reg_loss_weight=CONFIG['rpn_reg_loss_weight'],
            rpn_training_pos_iou_threshold=CONFIG['rpn_pos_iou_threshold'],
            rpn_training_neg_iou_threshold=CONFIG['rpn_neg_iou_threshold'],
            rpn_training_total_num_samples=CONFIG['rpn_total_sample_number'],
            rpn_training_max_pos_samples=CONFIG['rpn_pos_sample_max_number'],
        ),
        roi_training_model=RoiTrainingModel(
            num_classes=CONFIG['num_classes'],
            cls_loss_weight=CONFIG['roi_cls_loss_weight'],
            reg_loss_weight=CONFIG['roi_reg_loss_weight'],
            roi_training_pos_iou_threshold=CONFIG['roi_pos_iou_threshold'],
            roi_training_neg_iou_threshold=CONFIG['roi_neg_iou_threshold'],
            roi_training_total_num_samples=CONFIG['roi_total_sample_number'],
            roi_training_max_pos_samples=CONFIG['roi_pos_sample_max_number']
        ),
    )
    return base_model, training_model


def _get_vgg16_faster_rcnn_model():
    base_model = BaseFasterRcnnModel(
        ratios=CONFIG['ratios'],
        scales=CONFIG['scales'],
        extractor=Vgg16Extractor(),
        extractor_stride=CONFIG['extractor_stride'],

        weight_decay=CONFIG['weight_decay'],
        rpn_proposal_num_pre_nms_train=CONFIG['rpn_proposal_train_pre_nms_sample_number'],
        rpn_proposal_num_post_nms_train=CONFIG['rpn_proposal_train_after_nms_sample_number'],
        rpn_proposal_num_pre_nms_test=CONFIG['rpn_proposal_test_pre_nms_sample_number'],
        rpn_proposal_num_post_nms_test=CONFIG['rpn_proposal_test_after_nms_sample_number'],
        rpn_proposal_nms_iou_threshold=CONFIG['rpn_proposal_nms_iou_threshold'],

        roi_pool_size=CONFIG['roi_pooling_size'],
        num_classes=CONFIG['num_classes'],
        roi_head_keep_dropout_rate=CONFIG['roi_head_keep_dropout_rate'],
    )
    training_model = FasterRcnnTrainingModel(
        rpn_training_model=RpnTrainingModel(
            cls_loss_weight=CONFIG['rpn_cls_loss_weight'],
            reg_loss_weight=CONFIG['rpn_reg_loss_weight'],
            sigma=CONFIG['rpn_sigma'],
            rpn_training_pos_iou_threshold=CONFIG['rpn_pos_iou_threshold'],
            rpn_training_neg_iou_threshold=CONFIG['rpn_neg_iou_threshold'],
            rpn_training_total_num_samples=CONFIG['rpn_total_sample_number'],
            rpn_training_max_pos_samples=CONFIG['rpn_pos_sample_max_number'],
        ),
        roi_training_model=RoiTrainingModel(
            num_classes=CONFIG['num_classes'],
            cls_loss_weight=CONFIG['roi_cls_loss_weight'],
            reg_loss_weight=CONFIG['roi_reg_loss_weight'],
            sigma=CONFIG['roi_sigma'],
            roi_training_pos_iou_threshold=CONFIG['roi_pos_iou_threshold'],
            roi_training_neg_iou_threshold=CONFIG['roi_neg_iou_threshold'],
            roi_training_total_num_samples=CONFIG['roi_total_sample_number'],
            roi_training_max_pos_samples=CONFIG['roi_pos_sample_max_number']
        ),
    )
    return base_model, training_model


def _get_default_optimizer():
    lr = tf.train.exponential_decay(CONFIG['learning_rate_start'],
                                    tf.train.get_or_create_global_step(),
                                    CONFIG['learning_rate_decay_steps'],
                                    CONFIG['learning_rate_decay_rate'],
                                    True)
    return tf.train.MomentumOptimizer(lr, momentum=CONFIG['optimizer_momentum'])


def _get_training_dataset(preprocessing_type='caffe'):
    # tf_records_list = ['/home/tensorflow05/data/VOCdevkit/tf_eager_records/pascal_trainval_00.tfrecords',
    #                    '/home/tensorflow05/data/VOCdevkit/tf_eager_records/pascal_trainval_01.tfrecords',
    #                    '/home/tensorflow05/data/VOCdevkit/tf_eager_records/pascal_trainval_02.tfrecords',
    #                    '/home/tensorflow05/data/VOCdevkit/tf_eager_records/pascal_trainval_03.tfrecords',
    #                    '/home/tensorflow05/data/VOCdevkit/tf_eager_records/pascal_trainval_04.tfrecords',
    #                    ]
    # tf_records_list = [
    #     '/ssd/zhangyiyang/tf_eager_object_detection/VOCdevkit/tf_eager_records/pascal_trainval_00.tfrecords',
    #     '/ssd/zhangyiyang/tf_eager_object_detection/VOCdevkit/tf_eager_records/pascal_trainval_01.tfrecords',
    #     '/ssd/zhangyiyang/tf_eager_object_detection/VOCdevkit/tf_eager_records/pascal_trainval_02.tfrecords',
    #     '/ssd/zhangyiyang/tf_eager_object_detection/VOCdevkit/tf_eager_records/pascal_trainval_03.tfrecords',
    #     '/ssd/zhangyiyang/tf_eager_object_detection/VOCdevkit/tf_eager_records/pascal_trainval_04.tfrecords',
    #     ]
    tf_records_list = ['D:\\data\\VOCdevkit\\tf_eager_records\\pascal_trainval_00.tfrecords',
                       'D:\\data\\VOCdevkit\\tf_eager_records\\pascal_trainval_01.tfrecords'
                       'D:\\data\\VOCdevkit\\tf_eager_records\\pascal_trainval_02.tfrecords'
                       'D:\\data\\VOCdevkit\\tf_eager_records\\pascal_trainval_03.tfrecords'
                       'D:\\data\\VOCdevkit\\tf_eager_records\\pascal_trainval_04.tfrecords'
                       ]
    return get_dataset(tf_records_list,
                       preprocessing_type=preprocessing_type,
                       min_size=CONFIG['image_min_size'], max_size=CONFIG['image_max_size'])


def _get_evaluating_dataset(preprocessing_type='caffe'):
    # tf_records_list = ['/home/tensorflow05/data/VOCdevkit/tf_eager_records/pascal_test_00.tfrecords',
    #                    '/home/tensorflow05/data/VOCdevkit/tf_eager_records/pascal_test_01.tfrecords',
    #                    '/home/tensorflow05/data/VOCdevkit/tf_eager_records/pascal_test_02.tfrecords',
    #                    '/home/tensorflow05/data/VOCdevkit/tf_eager_records/pascal_test_03.tfrecords',
    #                    '/home/tensorflow05/data/VOCdevkit/tf_eager_records/pascal_test_04.tfrecords',
    #                    ]
    tf_records_list = ['D:\\data\\VOCdevkit\\tf_eager_records\\pascal_test_00.tfrecords',
                       'D:\\data\\VOCdevkit\\tf_eager_records\\pascal_test_01.tfrecords'
                       'D:\\data\\VOCdevkit\\tf_eager_records\\pascal_test_02.tfrecords'
                       'D:\\data\\VOCdevkit\\tf_eager_records\\pascal_test_03.tfrecords'
                       'D:\\data\\VOCdevkit\\tf_eager_records\\pascal_test_04.tfrecords'
                       ]
    # tf_records_list = ['/ssd/zhangyiyang/tf_eager_object_detection/VOCdevkit/tf_eager_records/pascal_test_00
    # .tfrecords', '/ssd/zhangyiyang/tf_eager_object_detection/VOCdevkit/tf_eager_records/pascal_test_01.tfrecords',
    # '/ssd/zhangyiyang/tf_eager_object_detection/VOCdevkit/tf_eager_records/pascal_test_02.tfrecords',
    # '/ssd/zhangyiyang/tf_eager_object_detection/VOCdevkit/tf_eager_records/pascal_test_03.tfrecords',
    # '/ssd/zhangyiyang/tf_eager_object_detection/VOCdevkit/tf_eager_records/pascal_test_04.tfrecords', ]
    return get_dataset(tf_records_list,
                       min_size=CONFIG['image_min_size'], max_size=CONFIG['image_max_size'],
                       preprocessing_type=preprocessing_type,
                       argument=False, )


class MeanOps:
    def __init__(self):
        self.total = .0
        self.cnt = 0

    def mean(self):
        if self.cnt == 0:
            return None
        return self.total / self.cnt

    def update(self, cur):
        self.total += cur
        self.cnt += 1

    def reset(self):
        self.total = .0
        self.cnt = 0


def train_one_epoch(dataset, base_model, training_model, optimizer,
                    logging_every_n_steps=20,
                    summary_every_n_steps=50,
                    saver=None, save_every_n_steps=2500, save_path=None):
    idx = 0

    rpn_cls_mean = MeanOps()
    rpn_reg_mean = MeanOps()
    roi_cls_mean = MeanOps()
    roi_reg_mean = MeanOps()
    total_mean = MeanOps()

    for image, gt_bboxes, gt_labels, _ in tqdm(dataset):
        gt_bboxes = tf.squeeze(gt_bboxes, axis=0)
        gt_labels = tf.to_int32(tf.squeeze(gt_labels, axis=0))
        with tf.GradientTape() as tape:
            shape, anchors, rpn_score, rpn_txtytwth, rpn_proposals, roi_score, roi_txtytwth = base_model(image,
                                                                                                         True)
            rpn_cls_loss, rpn_reg_loss, roi_cls_loss, roi_reg_loss = training_model((gt_bboxes, gt_labels,
                                                                                     shape, anchors,
                                                                                     rpn_score, rpn_txtytwth,
                                                                                     rpn_proposals,
                                                                                     roi_score, roi_txtytwth))
            l2_loss = tf.add_n(base_model.losses)
            total_loss = rpn_cls_loss + rpn_reg_loss + roi_cls_loss + roi_reg_loss
            #             total_loss = rpn_cls_loss + rpn_reg_loss
            #             total_loss = roi_cls_loss + roi_reg_loss
            rpn_cls_mean.update(rpn_cls_loss)
            rpn_reg_mean.update(rpn_reg_loss)
            roi_cls_mean.update(roi_cls_loss)
            roi_reg_mean.update(roi_reg_loss)
            total_mean.update(total_loss)

            train_step(base_model, total_loss, tape, optimizer)

        if idx % summary_every_n_steps == 0:
            summary.scalar("rpn_cls_loss", rpn_cls_mean.mean())
            summary.scalar("rpn_reg_loss", rpn_reg_mean.mean())
            summary.scalar("roi_cls_loss", roi_cls_mean.mean())
            summary.scalar("roi_reg_loss", roi_reg_mean.mean())
            summary.scalar("l2_loss", l2_loss)
            summary.scalar("total_loss", total_mean.mean())

        if idx % logging_every_n_steps == 0:
            tf_logging.info('steps %d loss: %.4f, %.4f, %.4f, %.4f, %.4f, %.4f' % (idx + 1,
                                                                                   rpn_cls_loss, rpn_reg_loss,
                                                                                   roi_cls_loss, roi_reg_loss,
                                                                                   tf.add_n(base_model.losses),
                                                                                   total_loss)
                            )
            pred_bboxes, pred_labels, pred_scores = post_ops_prediction(
                (rpn_proposals, roi_score, roi_txtytwth, shape),
                num_classes=CONFIG['num_classes'],
                max_num_per_class=CONFIG['max_objects_per_class_per_image'],
                max_num_per_image=CONFIG['max_objects_per_image'],
                nms_iou_threshold=CONFIG['predictions_nms_iou_threshold'],
                score_threshold=0.3,
            )
            gt_image = show_one_image(tf.squeeze(image, axis=0).numpy(), gt_bboxes.numpy(), gt_labels.numpy())
            tf.contrib.summary.image("gt_image", tf.expand_dims(gt_image, axis=0))
            if pred_bboxes is not None:
                pred_image = show_one_image(tf.squeeze(image, axis=0).numpy(), pred_bboxes.numpy(),
                                            pred_labels.numpy())
                tf.contrib.summary.image("pred_image", tf.expand_dims(pred_image, axis=0))

        if saver is not None and save_path is not None and idx % save_every_n_steps == 0:
            saver.save(os.path.join(save_path, 'model.ckpt'), global_step=tf.train.get_or_create_global_step())

        idx += 1


def train_eval(training_dataset, evaluating_dataset, base_model, training_model, optimizer,
               logging_every_n_steps=100,
               save_every_n_steps=2000,
               summary_every_n_steps=10,
               # train_dir='/home/tensorflow05/zyy/tf_eager_object_detection/logs-end2end',
               # val_dir='/home/tensorflow05/zyy/tf_eager_object_detection/logs-end2end/val',
               # ckpt_dir='/home/tensorflow05/zyy/tf_eager_object_detection/logs-end2end',
               train_dir='/ssd/zhangyiyang/tf_eager_object_detection/logs-end2end',
               val_dir='/ssd/zhangyiyang/tf_eager_object_detection/logs-end2end/val',
               ckpt_dir='/ssd/zhangyiyang/tf_eager_object_detection/logs-end2end',
               # train_dir='E:\\PycharmProjects\\tf_eager_object_detection\\logs',
               # val_dir='E:\\PycharmProjects\\tf_eager_object_detection\\logs\\val',

               ):
    saver = eager_saver.Saver(base_model.variables)

    # load saved ckpt files
    if tf.train.latest_checkpoint(ckpt_dir) is not None:
        saver.restore(tf.train.latest_checkpoint(ckpt_dir))
        tf_logging.info('restore from {}...'.format(tf.train.latest_checkpoint(ckpt_dir)))

    tf.train.get_or_create_global_step()
    train_writer = tf.contrib.summary.create_file_writer(train_dir, flush_millis=100000)
    val_writer = tf.contrib.summary.create_file_writer(val_dir, flush_millis=10000)
    for i in range(CONFIG['epochs']):
        tf_logging.info('epoch %d starting...' % (i + 1))
        start = time.time()
        with train_writer.as_default(), summary.always_record_summaries():
            train_one_epoch(training_dataset, base_model, training_model, optimizer,
                            logging_every_n_steps=logging_every_n_steps,
                            summary_every_n_steps=summary_every_n_steps,
                            saver=saver,
                            save_every_n_steps=save_every_n_steps,
                            save_path=ckpt_dir,
                            )
        train_end = time.time()
        tf_logging.info('epoch %d training finished, costing %d seconds, start evaluating...' % (i + 1,
                                                                                                 train_end - start))
        with val_writer.as_default():
            res = evaluate(evaluating_dataset, cur_base_model)
            tf_logging.info('epoch %d evaluating finished, costing %d seconds, '
                            'current mAP is %.4f' % (i + 1, time.time() - train_end, res['map']))


def evaluate(dataset, base_faster_rcnn_model, use_07_metric=False):
    gt_bboxes = []
    gt_labels = []
    pred_bboxes = []
    pred_labels = []
    pred_scores = []

    useless_pics = 0

    for cur_image, cur_gt_bboxes, cur_gt_labels, _ in tqdm(dataset):
        cur_gt_bboxes = tf.squeeze(cur_gt_bboxes, axis=0)
        cur_gt_labels = tf.to_int32(tf.squeeze(cur_gt_labels, axis=0))

        image_shape, _, _, _, rpn_proposals_bboxes, roi_score, roi_bboxes_txtytwth = base_faster_rcnn_model(cur_image,
                                                                                                            False)
        cur_pred_bboxes, cur_pred_labels, cur_pred_scores = post_ops_prediction((rpn_proposals_bboxes,
                                                                                 roi_score, roi_bboxes_txtytwth,
                                                                                 image_shape),
                                                                                num_classes=CONFIG['num_classes'],
                                                                                max_num_per_class=CONFIG[
                                                                                    'max_objects_per_class_per_image'],
                                                                                max_num_per_image=CONFIG[
                                                                                    'max_objects_per_image'],
                                                                                nms_iou_threshold=CONFIG[
                                                                                    'predictions_nms_iou_threshold'],
                                                                                score_threshold=0.05,
                                                                                )
        if cur_pred_bboxes is not None:
            useless_pics += 1
            print(useless_pics)
            gt_bboxes.append(cur_gt_bboxes.numpy())
            gt_labels.append(cur_gt_labels.numpy())
            pred_bboxes.append(cur_pred_bboxes.numpy())
            pred_labels.append(cur_pred_labels.numpy())
            pred_scores.append(cur_pred_scores.numpy())

    return eval_detection_voc(
        pred_bboxes, pred_labels, pred_scores,
        gt_bboxes, gt_labels,
        gt_difficults=None,
        iou_thresh=CONFIG['evaluate_iou_threshold'],
        use_07_metric=use_07_metric)


if __name__ == '__main__':
    tf_logging.set_verbosity(tf_logging.INFO)

    cur_base_model, cur_training_model = _get_vgg16_faster_rcnn_model()
    cur_training_dataset = _get_training_dataset('caffe')
    cur_evaluation_dataset = _get_evaluating_dataset('caffe')

    # cur_base_model, cur_training_model = _get_resnet101_faster_rcnn_model()
    # cur_training_dataset = _get_training_dataset('tf')
    # cur_evaluation_dataset = _get_evaluating_dataset('tf')

    cur_optimizer = _get_default_optimizer()
    train_eval(cur_training_dataset, cur_evaluation_dataset, cur_base_model, cur_training_model, cur_optimizer)
