#  Copyright (c) 2018 PaddlePaddle Authors. All Rights Reserve.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
All layers just related to the detection neural network.
"""

from layer_function_generator import generate_layer_fn
from ..layer_helper import LayerHelper
import nn
import ops
import tensor
import math

__all__ = [
    'prior_box',
    'bipartite_match',
    'target_assign',
    'detection_output',
    'ssd_loss',
]

__auto__ = [
    'iou_similarity',
    'box_coder',
]

__all__ += __auto__

for _OP in set(__auto__):
    globals()[_OP] = generate_layer_fn(_OP)


def detection_output(scores,
                     loc,
                     prior_box,
                     prior_box_var,
                     background_label=0,
                     nms_threshold=0.3,
                     nms_top_k=400,
                     keep_top_k=200,
                     score_threshold=0.01,
                     nms_eta=1.0):
    """
    **Detection Output Layer**

    This layer applies the NMS to the output of network and computes the 
    predict bounding box location. The output's shape of this layer could
    be zero if there is no valid bounding box.

    Args:
        scores(Variable): A 3-D Tensor with shape [N, C, M] represents the
            predicted confidence predictions. N is the batch size, C is the
            class number, M is number of bounding boxes. For each category
            there are total M scores which corresponding M bounding boxes.
        loc(Variable): A 3-D Tensor with shape [N, M, 4] represents the
            predicted locations of M bounding bboxes. N is the batch size,
            and each bounding box has four coordinate values and the layout
            is [xmin, ymin, xmax, ymax].
        prior_box(Variable): A 2-D Tensor with shape [M, 4] holds M boxes,
            each box is represented as [xmin, ymin, xmax, ymax],
            [xmin, ymin] is the left top coordinate of the anchor box,
            if the input is image feature map, they are close to the origin
            of the coordinate system. [xmax, ymax] is the right bottom
            coordinate of the anchor box.
        prior_box_var(Variable): A 2-D Tensor with shape [M, 4] holds M group
            of variance.
        background_label(float): The index of background label,
            the background label will be ignored. If set to -1, then all
            categories will be considered.
        nms_threshold(float): The threshold to be used in NMS.
        nms_top_k(int): Maximum number of detections to be kept according
            to the confidences aftern the filtering detections based on
            score_threshold.
        keep_top_k(int): Number of total bboxes to be kept per image after
            NMS step. -1 means keeping all bboxes after NMS step.
        score_threshold(float): Threshold to filter out bounding boxes with
            low confidence score. If not provided, consider all boxes.
        nms_eta(float): The parameter for adaptive NMS.

    Returns:
        The detected bounding boxes which are a Tensor.

    Examples:
        .. code-block:: python

        pb = layers.data(name='prior_box', shape=[10, 4],
                         append_batch_size=False, dtype='float32')
        pbv = layers.data(name='prior_box_var', shape=[10, 4],
                          append_batch_size=False, dtype='float32')
        loc = layers.data(name='target_box', shape=[21, 4],
                          append_batch_size=False, dtype='float32')
        scores = layers.data(name='scores', shape=[2, 21, 10],
                          append_batch_size=False, dtype='float32')
        nmsed_outs = fluid.layers.detection_output(scores=scores,
                                       loc=loc,
                                       prior_box=pb,
                                       prior_box_var=pbv)
    """

    helper = LayerHelper("detection_output", **locals())
    decoded_box = box_coder(
        prior_box=prior_box,
        prior_box_var=prior_box_var,
        target_box=loc,
        code_type='decode_center_size')

    nmsed_outs = helper.create_tmp_variable(dtype=decoded_box.dtype)
    helper.append_op(
        type="multiclass_nms",
        inputs={'Scores': scores,
                'BBoxes': decoded_box},
        outputs={'Out': nmsed_outs},
        attrs={
            'background_label': 0,
            'nms_threshold': nms_threshold,
            'nms_top_k': nms_top_k,
            'keep_top_k': keep_top_k,
            'score_threshold': score_threshold,
            'nms_eta': 1.0
        })
    return nmsed_outs


def prior_box(inputs,
              image,
              min_ratio,
              max_ratio,
              aspect_ratios,
              base_size,
              steps=None,
              step_w=None,
              step_h=None,
              offset=0.5,
              variance=[0.1, 0.1, 0.1, 0.1],
              flip=False,
              clip=False,
              min_sizes=None,
              max_sizes=None,
              name=None):
    """
    **Prior_boxes**

    Generate prior boxes for SSD(Single Shot MultiBox Detector)
    algorithm. The details of this algorithm, please refer the
    section 2.2 of SSD paper (SSD: Single Shot MultiBox Detector)
    <https://arxiv.org/abs/1512.02325>`_ .
    
    Args:
       inputs(list): The list of input Variables, the format
            of all Variables is NCHW.
       image(Variable): The input image data of PriorBoxOp,
            the layout is NCHW.
       min_ratio(int): the min ratio of generated prior boxes.
       max_ratio(int): the max ratio of generated prior boxes.
       aspect_ratios(list): the aspect ratios of generated prior
            boxes. The length of input and aspect_ratios must be equal.
       base_size(int): the base_size is used to get min_size
            and max_size according to min_ratio and max_ratio.
       step_w(list, optional, default=None): Prior boxes step
            across width. If step_w[i] == 0.0, the prior boxes step
            across width of the inputs[i] will be automatically calculated.
       step_h(list, optional, default=None): Prior boxes step
            across height, If step_h[i] == 0.0, the prior boxes
            step across height of the inputs[i] will be automatically calculated.
       offset(float, optional, default=0.5): Prior boxes center offset.
       variance(list, optional, default=[0.1, 0.1, 0.1, 0.1]): the variances
            to be encoded in prior boxes.
       flip(bool, optional, default=False): Whether to flip
            aspect ratios.
       clip(bool, optional, default=False): Whether to clip
            out-of-boundary boxes.
       min_sizes(list, optional, default=None): If `len(inputs) <=2`,
            min_sizes must be set up, and the length of min_sizes
            should equal to the length of inputs.
       max_sizes(list, optional, default=None): If `len(inputs) <=2`,
            max_sizes must be set up, and the length of min_sizes
            should equal to the length of inputs.
       name(str, optional, None): Name of the prior box layer.
    
    Returns:
        boxes(Variable): the output prior boxes of PriorBoxOp.
             The layout is [num_priors, 4]. num_priors is the total
             box count of each position of inputs.
        Variances(Variable): the expanded variances of PriorBoxOp.
             The layout is [num_priors, 4]. num_priors is the total
             box count of each position of inputs
    
    Examples:
        .. code-block:: python
    
          prior_box(
             inputs = [conv1, conv2, conv3, conv4, conv5, conv6],
             image = data,
             min_ratio = 20, # 0.20
             max_ratio = 90, # 0.90
             offset = 0.5,
             base_size = 300,
             variance = [0.1,0.1,0.1,0.1],
             aspect_ratios = [[2.], [2., 3.], [2., 3.], [2., 3.], [2.], [2.]],
             flip=True,
             clip=True)
    """

    def _prior_box_(input,
                    image,
                    min_sizes,
                    max_sizes,
                    aspect_ratios,
                    variance,
                    flip=False,
                    clip=False,
                    step_w=0.0,
                    step_h=0.0,
                    offset=0.5,
                    name=None):
        helper = LayerHelper("prior_box", **locals())
        dtype = helper.input_dtype()

        box = helper.create_tmp_variable(dtype)
        var = helper.create_tmp_variable(dtype)
        helper.append_op(
            type="prior_box",
            inputs={"Input": input,
                    "Image": image},
            outputs={"Boxes": box,
                     "Variances": var},
            attrs={
                'min_sizes': min_sizes,
                'max_sizes': max_sizes,
                'aspect_ratios': aspect_ratios,
                'variances': variance,
                'flip': flip,
                'clip': clip,
                'step_w': step_w,
                'step_h': step_h,
                'offset': offset
            })
        return box, var

    def _reshape_with_axis_(input, axis=1):
        if not (axis > 0 and axis < len(input.shape)):
            raise ValueError("The axis should be smaller than "
                             "the arity of input and bigger than 0.")
        new_shape = [
            -1, reduce(lambda x, y: x * y, input.shape[axis:len(input.shape)])
        ]
        out = ops.reshape(x=input, shape=new_shape)
        return out

    assert isinstance(inputs, list), 'inputs should be a list.'
    num_layer = len(inputs)

    if num_layer <= 2:
        assert min_sizes is not None and max_sizes is not None
        assert len(min_sizes) == num_layer and len(max_sizes) == num_layer
    else:
        min_sizes = []
        max_sizes = []
        step = int(math.floor(((max_ratio - min_ratio)) / (num_layer - 2)))
        for ratio in xrange(min_ratio, max_ratio + 1, step):
            min_sizes.append(base_size * ratio / 100.)
            max_sizes.append(base_size * (ratio + step) / 100.)
        min_sizes = [base_size * .10] + min_sizes
        max_sizes = [base_size * .20] + max_sizes

    if aspect_ratios:
        if not (isinstance(aspect_ratios, list) and
                len(aspect_ratios) == num_layer):
            raise ValueError(
                'aspect_ratios should be list and the length of inputs '
                'and aspect_ratios should be the same.')
    if step_h:
        if not (isinstance(step_h, list) and len(step_h) == num_layer):
            raise ValueError(
                'step_h should be list and the length of inputs and '
                'step_h should be the same.')
    if step_w:
        if not (isinstance(step_w, list) and len(step_w) == num_layer):
            raise ValueError(
                'step_w should be list and the length of inputs and '
                'step_w should be the same.')
    if steps:
        if not (isinstance(steps, list) and len(steps) == num_layer):
            raise ValueError(
                'steps should be list and the length of inputs and '
                'step_w should be the same.')
        step_w = steps
        step_h = steps

    box_results = []
    var_results = []
    for i, input in enumerate(inputs):
        min_size = min_sizes[i]
        max_size = max_sizes[i]
        aspect_ratio = []
        if not isinstance(min_size, list):
            min_size = [min_size]
        if not isinstance(max_size, list):
            max_size = [max_size]
        if aspect_ratios:
            aspect_ratio = aspect_ratios[i]
            if not isinstance(aspect_ratio, list):
                aspect_ratio = [aspect_ratio]

        box, var = _prior_box_(input, image, min_size, max_size, aspect_ratio,
                               variance, flip, clip, step_w[i]
                               if step_w else 0.0, step_h[i]
                               if step_w else 0.0, offset)

        box_results.append(box)
        var_results.append(var)

    if len(box_results) == 1:
        box = box_results[0]
        var = var_results[0]
    else:
        reshaped_boxes = []
        reshaped_vars = []
        for i in range(len(box_results)):
            reshaped_boxes.append(_reshape_with_axis_(box_results[i], axis=3))
            reshaped_vars.append(_reshape_with_axis_(var_results[i], axis=3))

        box = tensor.concat(reshaped_boxes)
        var = tensor.concat(reshaped_vars)

    return box, var


def bipartite_match(dist_matrix, name=None):
    """
    **Bipartite matchint operator**

    This operator is a greedy bipartite matching algorithm, which is used to
    obtain the matching with the maximum distance based on the input
    distance matrix. For input 2D matrix, the bipartite matching algorithm can
    find the matched column for each row, also can find the matched row for
    each column. And this operator only calculate matched indices from column
    to row. For each instance, the number of matched indices is the number of
    of columns of the input ditance matrix.
    
    There are two outputs to save matched indices and distance.
    A simple description, this algothrim matched the best (maximum distance)
    row entity to the column entity and the matched indices are not duplicated
    in each row of ColToRowMatchIndices. If the column entity is not matched
    any row entity, set -1 in ColToRowMatchIndices.
    
    Please note that the input DistMat can be LoDTensor (with LoD) or Tensor.
    If LoDTensor with LoD, the height of ColToRowMatchIndices is batch size.
    If Tensor, the height of ColToRowMatchIndices is 1.

    Args:
        dist_matrix(Variable): This input is a 2-D LoDTensor with shape
            [K, M]. It is pair-wise distance matrix between the entities
            represented by each row and each column. For example, assumed one
            entity is A with shape [K], another entity is B with shape [M]. The
            dist_matirx[i][j] is the distance between A[i] and B[j]. The bigger
            the distance is, the better macthing the pairs are. Please note,
            This tensor can contain LoD information to represent a batch of
            inputs. One instance of this batch can contain different numbers of
            entities.
    Returns:
        match_indices(Variable): A 2-D Tensor with shape [N, M] in int type.
            N is the batch size. If match_indices[i][j] is -1, it
            means B[j] does not match any entity in i-th instance.
            Otherwise, it means B[j] is matched to row
            match_indices[i][j] in i-th instance. The row number of
            i-th instance is saved in match_indices[i][j].
        match_distance(Variable): A 2-D Tensor with shape [N, M] in float type.
            N is batch size. If match_indices[i][j] is -1,
            match_distance[i][j] is also -1.0. Otherwise, assumed
            match_distance[i][j] = d, and the row offsets of each instance
            are called LoD. Then match_distance[i][j] = dist_matrix[d+LoD[i]][j].
    """
    helper = LayerHelper('bipartite_match', **locals())
    match_indices = helper.create_tmp_variable(dtype='int32')
    match_distance = helper.create_tmp_variable(dtype=dist_matrix.dtype)
    helper.append_op(
        type='bipartite_match',
        inputs={'DistMat': dist_matrix},
        outputs={
            'ColToRowMatchIndices': match_indices,
            'ColToRowMatchDist': match_distance
        })
    return match_indices, match_distance


def target_assign(input,
                  matched_indices,
                  negative_indices=None,
                  mismatch_value=None,
                  name=None):
    """
    **Target assigner operator**

    This operator can be, for given the target bounding boxes or labels,
    to assign classification and regression targets to each prediction as well as
    weights to prediction. The weights is used to specify which prediction would
    not contribute to training loss.
    
    For each instance, the output `out` and`out_weight` are assigned based on
    `match_indices` and `negative_indices`.
    Assumed that the row offset for each instance in `input` is called lod,
    this operator assigns classification/regression targets by performing the
    following steps:
    
    1. Assigning all outpts based on `match_indices`:
    
    If id = match_indices[i][j] > 0,
    
        out[i][j][0 : K] = X[lod[i] + id][j % P][0 : K]
        out_weight[i][j] = 1.
    
    Otherwise, 
    
        out[j][j][0 : K] = {mismatch_value, mismatch_value, ...}
        out_weight[i][j] = 0.
    
    2. Assigning out_weight based on `neg_indices` if `neg_indices` is provided:
    
    Assumed that the row offset for each instance in `neg_indices` is called neg_lod,
    for i-th instance and each `id` of neg_indices in this instance:
    
        out[i][id][0 : K] = {mismatch_value, mismatch_value, ...}
        out_weight[i][id] = 1.0

    Args:
       inputs (Variable): This input is a 3D LoDTensor with shape [M, P, K].
       matched_indices (Variable): Tensor<int>), The input matched indices
           is 2D Tenosr<int32> with shape [N, P], If MatchIndices[i][j] is -1,
           the j-th entity of column is not matched to any entity of row in
           i-th instance.
       negative_indices (Variable): The input negative example indices are
           an optional input with shape [Neg, 1] and int32 type, where Neg is
           the total number of negative example indices.
       mismatch_value (float32): Fill this value to the mismatched location.

    Returns:
       out (Variable): The output is a 3D Tensor with shape [N, P, K],
           N and P is the same as they are in `neg_indices`, K is the
           same as it in input of X. If `match_indices[i][j]`.
       out_weight (Variable): The weight for output with the shape of [N, P, 1].
    """
    helper = LayerHelper('target_assign', **locals())
    out = helper.create_tmp_variable(dtype=input.dtype)
    out_weight = helper.create_tmp_variable(dtype='float32')
    helper.append_op(
        type='target_assign',
        inputs={
            'X': input,
            'MatchIndices': matched_indices,
            'NegIndices': negative_indices
        },
        outputs={'Out': out,
                 'OutWeight': out_weight},
        attrs={'mismatch_value': mismatch_value})
    return out, out_weight


def ssd_loss(location,
             confidence,
             gt_box,
             gt_label,
             prior_box,
             prior_box_var=None,
             background_label=0,
             overlap_threshold=0.5,
             neg_pos_ratio=3.0,
             neg_overlap=0.5,
             loc_loss_weight=1.0,
             conf_loss_weight=1.0,
             match_type='per_prediction',
             mining_type='max_negative',
             sample_size=None):
    """
    **Multi-box loss layer for object dection algorithm of SSD**

    This layer is to compute dection loss for SSD given the location offset
    predictions, confidence predictions, prior boxes and ground-truth boudding
    boxes and labels, and the type of hard example mining. The returned loss
    is a weighted sum of the localization loss (or regression loss) and
    confidence loss (or classification loss) by performing the following steps:

    1. Find matched boundding box by bipartite matching algorithm.
      1.1 Compute IOU similarity between ground-truth boxes and prior boxes.
      1.2 Compute matched boundding box by bipartite matching algorithm.
    2. Compute confidence for mining hard examples
      2.1. Get the target label based on matched indices.
      2.2. Compute confidence loss.
    3. Apply hard example mining to get the negative example indices and update
       the matched indices.
    4. Assign classification and regression targets
      4.1. Encoded bbox according to the prior boxes.
      4.2. Assign regression targets.
      4.3. Assign classification targets.
    5. Compute the overall objective loss.
      5.1 Compute confidence loss.
      5.1 Compute localization loss.
      5.3 Compute the overall weighted loss.

    Args:
        location (Variable): The location predictions are a 3D Tensor with
            shape [N, Np, 4], N is the batch size, Np is total number of
            predictions for each instance. 4 is the number of coordinate values,
            the layout is [xmin, ymin, xmax, ymax].
        confidence (Variable): The confidence predictions are a 3D Tensor
            with shape [N, Np, C], N and Np are the same as they are in
            `location`, C is the class number.
        gt_box (Variable): The ground-truth boudding boxes (bboxes) are a 2D
            LoDTensor with shape [Ng, 4], Ng is the total number of ground-truth
            bboxes of mini-batch input.
        gt_label (Variable): The ground-truth labels are a 2D LoDTensor
            with shape [Ng, 1].
        prior_box (Variable): The prior boxes are a 2D Tensor with shape [Np, 4].
        prior_box_var (Variable): The variance of prior boxes are a 2D Tensor
            with shape [Np, 4].
        background_label (int): The index of background label, 0 by default.
        overlap_threshold (float): If match_type is 'per_prediction', use
            `overlap_threshold` to determine the extra matching bboxes when
             finding matched boxes. 0.5 by default.
        neg_pos_ratio (float): The ratio of the negative boxes to the positive
            boxes, used only when mining_type is max_negative, 3.0 by defalut.
        neg_overlap (float): The negative overlap upper bound for the unmatched
            predictions. Use only when mining_type is max_negative,
            0.5 by default.
        sample_size (int): The max sample size of negative box, used only when
            mining_type is hard_example.
        loc_loss_weight (float): Weight for localization loss, 1.0 by default.
        conf_loss_weight (float): Weight for confidence loss, 1.0 by default.
        match_type (str): The type of matching method during training, should
            be 'bipartite' or 'per_prediction'.
        mining_type (str): The hard example mining type, should be 'hard_example'
            or 'max_negative', now only support `max_negative`.

    Returns:
        Variable: The weighted sum of the localization loss and confidence loss,
            with shape [N * Np, 1], N and Np are the same as they are
            in `location`.

    Raises:
        ValueError: If mining_type is 'hard_example', now only support
            mining type of `max_negative`.

    Examples:
        .. code-block:: python

            pb = layers.data(
                name='prior_box',
                shape=[10, 4],
                append_batch_size=False,
                dtype='float32')
            pbv = layers.data(
                name='prior_box_var',
                shape=[10, 4],
                append_batch_size=False,
                dtype='float32')
            loc = layers.data(name='target_box', shape=[10, 4], dtype='float32')
            scores = layers.data(name='scores', shape=[10, 21], dtype='float32')
            gt_box = layers.data(
                name='gt_box', shape=[4], lod_level=1, dtype='float32')
            gt_label = layers.data(
                name='gt_label', shape=[1], lod_level=1, dtype='float32')
            loss = layers.ssd_loss(loc, scores, gt_box, gt_label, pb, pbv)
    """

    helper = LayerHelper('ssd_loss', **locals())
    if mining_type != 'max_negative':
        raise ValueError("Only support mining_type == max_negative now.")

    num, num_prior, num_class = confidence.shape

    def __reshape_to_2d(var):
        return ops.reshape(x=var, shape=[-1, var.shape[-1]])

    # 1. Find matched boundding box by prior box.
    #   1.1 Compute IOU similarity between ground-truth boxes and prior boxes.
    iou = iou_similarity(x=gt_box, y=prior_box)
    #   1.2 Compute matched boundding box by bipartite matching algorithm.
    matched_indices, matched_dist = bipartite_match(iou)

    # 2. Compute confidence for mining hard examples
    # 2.1. Get the target label based on matched indices
    gt_label = ops.reshape(x=gt_label, shape=gt_label.shape + (1, ))
    target_label, _ = target_assign(
        gt_label, matched_indices, mismatch_value=background_label)
    # 2.2. Compute confidence loss.
    # Reshape confidence to 2D tensor.
    confidence = __reshape_to_2d(confidence)
    target_label = tensor.cast(x=target_label, dtype='int64')
    target_label = __reshape_to_2d(target_label)
    conf_loss = nn.softmax_with_cross_entropy(confidence, target_label)

    # 3. Mining hard examples
    conf_loss = ops.reshape(x=conf_loss, shape=(num, num_prior))
    neg_indices = helper.create_tmp_variable(dtype='int32')
    dtype = matched_indices.dtype
    updated_matched_indices = helper.create_tmp_variable(dtype=dtype)
    helper.append_op(
        type='mine_hard_examples',
        inputs={
            'ClsLoss': conf_loss,
            'LocLoss': None,
            'MatchIndices': matched_indices,
            'MatchDist': matched_dist,
        },
        outputs={
            'NegIndices': neg_indices,
            'UpdatedMatchIndices': updated_matched_indices
        },
        attrs={
            'neg_pos_ratio': neg_pos_ratio,
            'neg_dist_threshold': neg_pos_ratio,
            'mining_type': mining_type,
            'sample_size': sample_size,
        })

    # 4. Assign classification and regression targets
    # 4.1. Encoded bbox according to the prior boxes.
    encoded_bbox = box_coder(
        prior_box=prior_box,
        prior_box_var=prior_box_var,
        target_box=gt_box,
        code_type='encode_center_size')
    # 4.2. Assign regression targets
    target_bbox, target_loc_weight = target_assign(
        encoded_bbox, updated_matched_indices, mismatch_value=background_label)
    # 4.3. Assign classification targets
    target_label, target_conf_weight = target_assign(
        gt_label,
        updated_matched_indices,
        negative_indices=neg_indices,
        mismatch_value=background_label)

    # 5. Compute loss.
    # 5.1 Compute confidence loss.
    target_label = __reshape_to_2d(target_label)
    target_label = tensor.cast(x=target_label, dtype='int64')
    conf_loss = nn.softmax_with_cross_entropy(confidence, target_label)
    target_conf_weight = __reshape_to_2d(target_conf_weight)
    conf_loss = conf_loss * target_conf_weight

    # 5.2 Compute regression loss.
    location = __reshape_to_2d(location)
    target_bbox = __reshape_to_2d(target_bbox)

    loc_loss = nn.smooth_l1(location, target_bbox)
    target_loc_weight = __reshape_to_2d(target_loc_weight)
    loc_loss = loc_loss * target_loc_weight

    # 5.3 Compute overall weighted loss.
    loss = conf_loss_weight * conf_loss + loc_loss_weight * loc_loss
    return loss