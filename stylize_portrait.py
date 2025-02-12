# Copyright (c) 2015-2016 Anish Athalye. Released under GPLv3.

import vgg

import tensorflow as tf
import numpy as np

from sys import stderr

CONTENT_LAYER = 'relu4_2'
STYLE_LAYERS = ('relu1_1', 'relu2_1', 'relu3_1', 'relu4_1', 'relu5_1')


try:
    reduce
except NameError:
    from functools import reduce


def stylize(network, initial, content, styles, iterations,
        content_weight, style_weight, style_blend_weights, tv_weight,
        learning_rate, print_iterations, checkpoint_iterations=None):
    """
    Stylize images.

    This function yields tuples (iteration, image); `iteration` is None
    if this is the final image (the last iteration).  Other tuples are yielded
    every `checkpoint_iterations` iterations.

    :rtype: iterator[tuple[int|None,image]]
    """
    shape = (1,) + content.shape
    style_shapes = [(1,) + style.shape for style in styles]
    content_features = {}
    style_features = [{} for _ in styles]

    # compute content features in feedforward mode
    g = tf.Graph()
    with g.as_default(), g.device('/gpu:0'), tf.Session() as sess:
        image = tf.placeholder('float', shape=shape)
        net, mean_pixel = vgg.net(network, image)
        content_pre = np.array([vgg.preprocess(content, mean_pixel)])
        content_features[CONTENT_LAYER] = net[CONTENT_LAYER].eval(
                feed_dict={image: content_pre})

    # compute style features in feedforward mode
    for i in range(len(styles)):
        g = tf.Graph()
        with g.as_default(), g.device('/gpu:0'), tf.Session() as sess:
            image = tf.placeholder('float', shape=style_shapes[i])
            net, _ = vgg.net(network, image)
            style_pre = np.array([vgg.preprocess(styles[i], mean_pixel)])
            for layer in STYLE_LAYERS:
                _, h, w, d = map(lambda i: i.value, net[layer].get_shape())
                style_features[i][layer] = _gram_matrix(net[layer], h * w, d).eval(
                        feed_dict={image: style_pre})
            # ###compute style content feature
            # style_features[i][CONTENT_LAYER] = net[CONTENT_LAYER].eval(
            #         feed_dict={image: style_pre})

    # make stylized image using backpropogation
    with tf.Graph().as_default():
        if initial is None:
            noise = np.random.normal(size=shape, scale=np.std(content) * 0.1)
            initial = tf.random_normal(shape) * 0.256
        else:
            initial = np.array([vgg.preprocess(initial, mean_pixel)])
            initial = initial.astype('float32')
        image = tf.Variable(initial)
        net, _ = vgg.net(network, image)

        # content loss
        content_loss = _content_loss_sum(net, content_features)
        # style loss
        style_loss = _style_loss_sum(net, style_features, style_blend_weights)
        # total variation denoising
        tv_y_size = _tensor_size(image[:,1:,:,:])
        tv_x_size = _tensor_size(image[:,:,1:,:])
        tv_loss = tv_weight * 2 * (
                (tf.nn.l2_loss(image[:,1:,:,:] - image[:,:shape[1]-1,:,:]) /
                    tv_y_size) +
                (tf.nn.l2_loss(image[:,:,1:,:] - image[:,:,:shape[2]-1,:]) /
                    tv_x_size))
        # overall loss
        loss = content_weight * content_loss + style_weight * style_loss + tv_loss

        # optimizer setup
        train_step = tf.train.AdamOptimizer(learning_rate).minimize(loss)

        def print_progress(i, last=False):
            #stderr.write('Iteration %d/%d\n' % (i + 1, iterations))
            if last or (print_iterations and i % print_iterations == 0):
                stderr.write('  content loss: %g\n' % content_loss.eval())
                stderr.write('    style loss: %g\n' % style_loss.eval())
                stderr.write('       tv loss: %g\n' % tv_loss.eval())
                stderr.write('    total loss: %g\n' % loss.eval())

        # optimization
        best_loss = float('inf')
        best = None
        with tf.Session() as sess:
            sess.run(tf.initialize_all_variables())
            for i in range(iterations):
                last_step = (i == iterations - 1)
                print_progress(i, last=last_step)
                train_step.run()

                if (checkpoint_iterations and i % checkpoint_iterations == 0) or last_step:
                    this_loss = loss.eval()
                    if this_loss < best_loss:
                        best_loss = this_loss
                        best = image.eval()
                    yield (
                        (None if last_step else i),
                        vgg.unprocess(best.reshape(shape[1:]), mean_pixel)
                    )
def _content_loss_sum(net, content):
    return tf.nn.l2_loss(
            net[CONTENT_LAYER] - content[CONTENT_LAYER]) / content[CONTENT_LAYER].size
        # ###introduce modified feature map
    	# mod = style_features[0][CONTENT_LAYER] / (
        # 	content_features[CONTENT_LAYER] + 1e-4)
    	# mod = np.maximum(
        # 	np.minimum(mod, np.full(mod.shape, 5, np.float32)),
        # 	np.full(mod.shape, 0.7, np.float32))
    	# mod = content_features[CONTENT_LAYER] * mod
        #
        # content_loss = content_weight * (2 * tf.nn.l2_loss(
        #         net[CONTENT_LAYER] - mod) /
        #         mod.size)

def _style_loss_sum(net, styles, weights):
    loss = 0
    for i in range(len(styles)):
        style_loss = []
        for layer in STYLE_LAYERS:
            _, h, w, d = map(lambda i: i.value, net[layer].get_shape())
            gram = _gram_matrix(net[layer], h * w, d)
            style_gram = styles[i][layer]
            style_loss.append(
                1./2 * tf.nn.l2_loss((gram - style_gram) / style_gram.size))
        loss += weights[i] * reduce(tf.add, style_loss)
    return loss

def _gram_matrix(x, area, number):
    feats = tf.reshape(x, (area, number))
    gram = tf.matmul(tf.transpose(feats), feats)
    return gram

def _tensor_size(tensor):
    from operator import mul
    return reduce(mul, (d.value for d in tensor.get_shape()), 1)
