# -*- coding: utf-8 -*-
"""
Module that acts as a factory to create segmentation models with a common
interface, regardless of the underlying implementation.

Many models are supported by using the segmentation model zoo:
https://github.com/qubvel/segmentation_models


@author: Pieter Roggemans
"""

import keras as kr

'''
preprocessing_fn = get_preprocessing('resnet34')
x = preprocessing_fn(x)
'''

def get_model(segmentation_model: str = 'unet_ternaus',
              backbone_name: str = None,
              input_width=256,
              input_height=256,
              n_channels=3,
              n_classes=1,
              init_model_weights: bool = False):

    if segmentation_model.lower() == 'deeplabv3plus':
        import model_deeplabv3plus as m
        return m.get_model(input_width=input_width, input_height=input_height,
                           n_channels=n_channels, n_classes=n_classes,
                           init_model_weights=init_model_weights)
    elif segmentation_model.lower() == 'unet':
        # These two unet variants are implemented in a seperate module
        if backbone_name.lower() == 'standard':
            import model_unet_standard as m
            return m.get_model(input_width=input_width, input_height=input_height,
                               n_channels=n_channels, n_classes=n_classes,
                               init_model_weights=init_model_weights)
        elif backbone_name.lower() == 'ternaus':
            import model_unet_ternaus as m
            return m.get_model(input_width=input_width, input_height=input_height,
                               n_channels=n_channels, n_classes=n_classes,
                               init_model_weights=init_model_weights)

        # Some other unet variants is implemented using the segmentation_models library
        from segmentation_models import Unet
        #from segmentation_models.backbones import get_preprocessing

        init_weights = None
        if init_model_weights:
            init_weights = 'imagenet'

        model = Unet(backbone_name=backbone_name,
                     input_shape=(input_width, input_height, n_channels),
                     classes=n_classes,
                     encoder_weights=init_weights)
        return model
    elif segmentation_model.lower() == 'pspnet':
        from segmentation_models import PSPNet
        #from segmentation_models.backbones import get_preprocessing

        init_weights = None
        if init_model_weights:
            init_weights = 'imagenet'

        model = PSPNet(backbone_name=backbone_name,
                       input_shape=(input_width, input_height, n_channels),
                       classes=n_classes,
                       encoder_weights=init_weights)
        return model
    elif segmentation_model.lower() == 'linknet':
        from segmentation_models import Linknet
        #from segmentation_models.backbones import get_preprocessing

        init_weights = None
        if init_model_weights:
            init_weights = 'imagenet'

        model = Linknet(backbone_name=backbone_name,
                        input_shape=(input_width, input_height, n_channels),
                        classes=n_classes,
                        encoder_weights=init_weights)
        return model
    else:
        raise Exception(f"Unknown segmentation_model: {segmentation_model}")

def compile_model(model,
                  optimizer,
                  loss_mode='binary_crossentropy',
                  metrics=None):

    if loss_mode == "bcedice":
        loss_func = dice_coef_loss_bce
    elif loss_mode == "binary_crossentropy":
        loss_func = "binary_crossentropy"
    else:
        raise Exception(f"Unknown loss function: {loss_mode}")

    # TODO: implement option to specify metrics...
    model.compile(optimizer=optimizer, loss=loss_func,
                  metrics=[jaccard_coef, jaccard_coef_flat,
                           jaccard_coef_int, dice_coef, 'accuracy', 'binary_accuracy'])

    return model

def load_model(model_to_use_filepath: str):
    model = kr.models.load_model(model_to_use_filepath,
                                 custom_objects={'jaccard_coef': jaccard_coef,
                                                 'jaccard_coef_flat': jaccard_coef_flat,
                                                 'jaccard_coef_int': jaccard_coef_int,
                                                 'dice_coef': dice_coef})

    return model

#------------------------------------------
# Loss functions
#------------------------------------------

def dice_coef_loss(y_true, y_pred):
    return 1 - dice_coef(y_true, y_pred)

def bootstrapped_crossentropy(y_true, y_pred, bootstrap_type='hard', alpha=0.95):
    target_tensor = y_true
    prediction_tensor = y_pred
    _epsilon = kr.backend.tensorflow_backend._to_tensor(kr.backend.epsilon(), prediction_tensor.dtype.base_dtype)
    prediction_tensor = kr.backend.tf.clip_by_value(prediction_tensor, _epsilon, 1 - _epsilon)
    prediction_tensor = kr.backend.tf.log(prediction_tensor / (1 - prediction_tensor))

    if bootstrap_type == 'soft':
        bootstrap_target_tensor = alpha * target_tensor + (1.0 - alpha) * kr.backend.tf.sigmoid(prediction_tensor)
    else:
        bootstrap_target_tensor = alpha * target_tensor + (1.0 - alpha) * kr.backend.tf.cast(
            kr.backend.tf.sigmoid(prediction_tensor) > 0.5, kr.backend.tf.float32)
    return kr.backend.mean(kr.backend.tf.nn.sigmoid_cross_entropy_with_logits(
        labels=bootstrap_target_tensor, logits=prediction_tensor))

def dice_coef_loss_bce(y_true, y_pred):
    dice = 0.5
    bce = 0.5
    bootstrapping = 'hard'
    alpha = 1.
    return bootstrapped_crossentropy(y_true, y_pred, bootstrapping, alpha) * bce + dice_coef_loss(y_true, y_pred) * dice

#------------------------------------------
# Metrics functions
#------------------------------------------

SMOOTH_LOSS = 1e-12

def jaccard_coef(y_true, y_pred):
    intersection = kr.backend.sum(y_true * y_pred, axis=[0, -1, -2])
    sum_ = kr.backend.sum(y_true + y_pred, axis=[0, -1, -2])

    jac = (intersection + SMOOTH_LOSS) / (sum_ - intersection + SMOOTH_LOSS)

    return kr.backend.mean(jac)

def jaccard_coef_int(y_true, y_pred):
    y_pred_pos = kr.backend.round(kr.backend.clip(y_pred, 0, 1))

    intersection = kr.backend.sum(y_true * y_pred_pos, axis=[0, -1, -2])
    sum_ = kr.backend.sum(y_true + y_pred_pos, axis=[0, -1, -2])
    jac = (intersection + SMOOTH_LOSS) / (sum_ - intersection + SMOOTH_LOSS)
    return kr.backend.mean(jac)

def jaccard_coef_flat(y_true, y_pred):
    y_true_f = kr.backend.flatten(y_true)
    y_pred_f = kr.backend.flatten(y_pred)
    intersection = kr.backend.sum(y_true_f * y_pred_f)
    return (intersection + SMOOTH_LOSS) / (kr.backend.sum(y_true_f) + kr.backend.sum(y_pred_f) - intersection + SMOOTH_LOSS)

def dice_coef(y_true, y_pred, smooth=1.0):
    y_true_f = kr.backend.flatten(y_true)
    y_pred_f = kr.backend.flatten(y_pred)
    intersection = kr.backend.sum(y_true_f * y_pred_f)
    return (2. * intersection + smooth) / (kr.backend.sum(y_true_f) + kr.backend.sum(y_pred_f) + smooth)

def pct_wrong(y_true, y_pred):
    y_pred_pos = kr.backend.round(kr.backend.clip(y_pred, 0, 1))

    intersection = kr.backend.sum(y_true * y_pred_pos, axis=[0, -1, -2])
    sum_ = kr.backend.sum(y_true + y_pred_pos, axis=[0, -1, -2])
    jac = (intersection + SMOOTH_LOSS) / (sum_ - intersection + SMOOTH_LOSS)
    return kr.backend.mean(jac)