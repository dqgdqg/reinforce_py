import numpy as np
import tensorflow as tf
from collections import deque


class ActorCritic(object):
    def __init__(self, input_dim, hidden_units, action_dim):
        self.input_dim = input_dim
        self.hidden_units = hidden_units
        self.action_dim = action_dim
        self.gamma = 0.99
        self.discount_factor = 0.99
        self.max_gradient = 5
        # counter
        self.train_episode = 0
        # buffer init
        self.buffer_reset()

        self.batch_size = 32

    def construct_model(self, gpu):
        if gpu == -1: # use CPU
            device = '/cpu:0'
            sess_config = tf.ConfigProto()
        else: # use GPU
            device = '/gpu:' + str(gpu)
            sess_config = tf.ConfigProto(log_device_placement=True,
                            allow_soft_placement=True)
            sess_config.gpu_options.allow_growth = True

        self.sess = tf.Session(config=sess_config)

        with tf.device(device):
            with tf.name_scope('model_inputs'):
                self.input_state = tf.placeholder(
                        tf.float32, [None, self.input_dim], name='input_state')
            with tf.variable_scope('actor_network'):
                self.logp = self.actor_network(self.input_state)
            with tf.variable_scope('critic_network'):
                self.state_value = self.critic_network(self.input_state)
            with tf.variable_scope('target_critic_network'):
                self.target_state_value = self.critic_network(self.input_state)

            # get network parameters
            actor_parameters = tf.get_collection(
                    tf.GraphKeys.TRAINABLE_VARIABLES, scope='actor_network')
            critic_parameters = tf.get_collection(
                    tf.GraphKeys.TRAINABLE_VARIABLES, scope='critic_network')

            # self.discounted_rewards = tf.placeholder(tf.float32, [None, 1])
            self.taken_action = tf.placeholder(tf.int32, [None,])

            self.s_target_v = tf.placeholder(tf.float32, [None, 1])
            self.t_target_v = tf.placeholder(tf.float32, [None, 1])

            # optimizer
            self.optimizer = tf.train.RMSPropOptimizer(learning_rate=1e-4, decay=0.9)
            # actor loss
            self.actor_loss = tf.nn.sparse_softmax_cross_entropy_with_logits(self.logp, self.taken_action)
            # advantage
            self.advantages = (self.s_target_v - self.state_value)[:,0]
            # actor gradient
            actor_gradients = tf.gradients(self.actor_loss, actor_parameters, self.advantages)
            self.actor_gradients = zip(actor_gradients, actor_parameters)

            # policy gradient
            for i, (grad, var) in enumerate(self.actor_gradients):
                if grad is not None:
                    # pg_grad = grad * self.advantages
                    # gradient clipping
                    self.actor_gradients[i] = (tf.clip_by_value(
                            grad, -self.max_gradient, self.max_gradient), var)

            # critic loss
            self.critic_loss = tf.reduce_mean(tf.square(self.t_target_v - self.state_value))
            # critic gradient
            self.critic_gradients = self.optimizer.compute_gradients(self.critic_loss, critic_parameters)
            # clip gradient
            for i, (grad, var) in enumerate(self.critic_gradients):
                if grad is not None:
                    self.critic_gradients[i] = (tf.clip_by_value(
                            grad, -self.max_gradient, self.max_gradient), var)

            with tf.name_scope('train_actor_critic'):
                # train operation
                self.train_actor = self.optimizer.apply_gradients(self.actor_gradients)
                self.train_critic = self.optimizer.apply_gradients(self.critic_gradients)

            # update targer network parameters
            with tf.name_scope("update_target_network"):
                self.target_network_update = []

                # same for the critic network
                critic_parameters = tf.get_collection(
                        tf.GraphKeys.TRAINABLE_VARIABLES, scope="critic_network")
                target_critic_parameters = tf.get_collection(
                        tf.GraphKeys.TRAINABLE_VARIABLES, scope="target_critic_network")
                for v_source, v_target in zip(critic_parameters, target_critic_parameters):
                    # this is equivalent to target = (1-alpha) * target + alpha * source
                    update_op = v_target.assign_sub(0.01 * (v_target - v_source))
                    self.target_network_update.append(update_op)
                # group all assignment operations together
                self.target_network_update = tf.group(*self.target_network_update)

    def init_model(self):
        # initialize variables
        init_op = tf.global_variables_initializer()
        self.sess.run(init_op)

    def sample_action(self, state):
        def softmax(x):
            max_x = np.amax(x)
            e = np.exp(x - max_x)
            return e / np.sum(e)

        logp = self.sess.run(self.logp, {self.input_state: state})[0]
        prob = softmax(logp) - 1e-5
        action = np.argmax(np.random.multinomial(1, prob))
        return action

    def update_model(self):
        state_buffer = np.array(self.state_buffer)
        action_buffer = np.array(self.action_buffer)
        # discounted_rewards_buffer = np.vstack(self.reward_discount())
        reward_buffer = np.vstack(self.reward_buffer)
        next_state_buffer = np.array(self.next_state_buffer)
        done_buffer = np.vstack(self.done_buffer)

        ep_steps = len(action_buffer)
        shuffle_index = np.arange(ep_steps)
        np.random.shuffle(shuffle_index)

        for i in range(0, ep_steps, self.batch_size):

            end_index = i+self.batch_size if i+self.batch_size <= ep_steps else ep_steps
            batch_index = shuffle_index[i:end_index]
            # get batch from buffer
            input_state = state_buffer[batch_index]
            taken_action = action_buffer[batch_index]
            # discounted_rewards = discounted_rewards_buffer[batch_index]
            reward = reward_buffer[batch_index]
            next_state = next_state_buffer[batch_index]
            done = done_buffer[batch_index]

            s_target_next_v = self.sess.run(self.state_value, feed_dict={
                self.input_state: next_state
            })
            # s_target_v = discounted_rewards + (self.gamma * s_target_next_v) * -done
            s_target_v = reward + (self.gamma * s_target_next_v) * -done

            t_target_next_v = self.sess.run(self.target_state_value, feed_dict={
                self.input_state: next_state
            })
            # t_target_v = discounted_rewards + (self.gamma * t_target_next_v) * -done
            t_target_v = reward + (self.gamma * t_target_next_v) * -done

            # train!
            self.sess.run([
                self.train_actor,
                self.train_critic
            ], feed_dict={
                self.input_state: input_state,
                self.taken_action: taken_action,
                # self.discounted_rewards: discounted_rewards,
                self.s_target_v: s_target_v,
                self.t_target_v: t_target_v
            })

        # update target network
        self.sess.run(self.target_network_update)

        # cleanup job
        self.buffer_reset()

        self.train_episode += 1

    def store_rollout(self, state, action, reward, next_state, done):
        self.action_buffer.append(action)
        self.reward_buffer.append(reward)
        self.state_buffer.append(state)
        self.next_state_buffer.append(next_state)
        self.done_buffer.append(done)

    def buffer_reset(self):
        self.state_buffer  = []
        self.reward_buffer = []
        self.action_buffer = []
        self.next_state_buffer = []
        self.done_buffer = []

    def reward_discount(self):
        r = self.reward_buffer
        d_r = np.zeros_like(r)
        running_add = 0
        for t in range(len(r))[::-1]:
            if r[t] != 0:
                running_add = 0 # game boundary. reset the running add
            running_add = r[t] + running_add * self.discount_factor
            d_r[t] += running_add
        # standardize the rewards
        d_r -= np.mean(d_r)
        d_r /= np.std(d_r)
        return d_r

    def actor_network(self, input_state):
        w1 = tf.Variable(tf.div(tf.random_normal(
            [self.input_dim, self.hidden_units]), np.sqrt(self.input_dim)),name='w1')
        b1 = tf.Variable(tf.constant(0.0, shape=[self.hidden_units]), name='b1')
        h1 = tf.nn.relu(tf.matmul(input_state, w1) + b1)
        w2 = tf.Variable(tf.div(tf.random_normal(
            [self.hidden_units, self.action_dim]), np.sqrt(self.hidden_units)), name='w2')
        b2 = tf.Variable(tf.constant(0.0, shape=[self.action_dim]), name='b2')
        logp = tf.matmul(h1, w2) + b2

        return logp

    def critic_network(self, input_state):
        w1 = tf.Variable(tf.div(tf.random_normal(
            [self.input_dim, self.hidden_units]), np.sqrt(self.input_dim)), name='w1')
        b1 = tf.Variable(tf.constant(0.0, shape=[self.hidden_units]), name='b1')
        h1 = tf.nn.relu(tf.matmul(input_state, w1) + b1)
        w2 = tf.Variable(tf.div(tf.random_normal(
            [self.hidden_units, 1]), np.sqrt(self.hidden_units)), name='w2')
        b2 = tf.Variable(tf.constant(0.0, shape=[1]), name='b2')
        state_value = tf.matmul(h1, w2) + b2

        return state_value
