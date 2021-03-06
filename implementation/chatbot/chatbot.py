import torch
import torch.nn as nn
from torch.autograd import Variable
from torch import optim
import torch.nn.functional as F
import random
import DataHub as DH
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
import pickle


### global setting
teacher_forcing_ratio = 0.5
use_cuda = 0
MAX_LENGTH = 128
EOS_token = 1


class EncoderRNN(nn.Module):
    def __init__(self, input_dim, hidden_dim, embedding, n_layers=1):
        super(EncoderRNN, self).__init__()
        self.n_layers = n_layers
        self.hidden_dim = hidden_dim
        # self.embedding = nn.Embedding(input_dim, hidden_dim)
        self.embedding = embedding
        self.gru = nn.GRU(hidden_dim, hidden_dim)

    def forward(self, _input, hidden):
        embedded = self.embedding(_input).view(1, 1, -1)
        output = embedded
        for i in range(self.n_layers):
            output, hidden = self.gru(output, hidden)
        return output, hidden

    def initHidden(self):
        result = Variable(torch.zeros(1, 1, self.hidden_dim))
        if use_cuda:
            return result.cuda()
        else:
            return result


class AttnDecoderRNN(nn.Module):
    def __init__(self, hidden_dim, output_dim, embedding, n_layers=1, dropout_p=0.1, max_length=MAX_LENGTH):
        super(AttnDecoderRNN, self).__init__()
        self.hidden_dim = hidden_dim
        self.output_dim = output_dim
        self.n_layers = n_layers
        self.dropout_p = dropout_p
        self.max_length = max_length

        # self.embedding = nn.Embedding(self.output_dim, self.hidden_dim)
        self.embedding = embedding
        self.emotion_embedding_dim = 16
        self.emotion_embedding = nn.Embedding(7, self.emotion_embedding_dim)
        self.attn = nn.Linear(self.hidden_dim * 2, self.max_length)
        self.attn_combine = nn.Linear(self.hidden_dim * 2 + self.emotion_embedding_dim, self.hidden_dim)
        self.dropout = nn.Dropout(self.dropout_p)
        self.gru = nn.GRU(self.hidden_dim, self.hidden_dim)
        self.out = nn.Linear(self.hidden_dim, self.output_dim)

    def forward(self, _input, hidden, encoder_output, encoder_outputs, emotion):
        embedded = self.embedding(_input).view(1, 1, -1)
        embedded = self.dropout(embedded)

        attn_weights = F.softmax(
            self.attn(torch.cat((embedded[0], hidden[0]), 1)))
        attn_applied = torch.bmm(attn_weights.unsqueeze(0),
                                 encoder_outputs.unsqueeze(0))

        output = torch.cat((embedded[0], attn_applied[0]), 1)
        output = torch.cat((output, self.emotion_embedding(emotion).view(1, -1)), 1)
        output = self.attn_combine(output).unsqueeze(0)

        for i in range(self.n_layers):
            output = F.relu(output)
            output, hidden = self.gru(output, hidden)

        output = F.log_softmax(self.out(output[0]))
        return output, hidden, attn_weights

    def initHidden(self):
        result = Variable(torch.zeros(1, 1, self.hidden_size))
        if use_cuda:
            return result.cuda()
        else:
            return result


def variableFromSentence(sentence):
    # sentence is a list of list of index
    result = Variable(torch.LongTensor(sentence).view(-1, 1))
    if use_cuda:
        return result.cuda()
    else:
        return result


def variablesFromPair(pair):
    input_variable = variableFromSentence(pair[0])
    target_variable = variableFromSentence(pair[1])
    return (input_variable, target_variable)


def train(input_variable, target_variable, encoder, decoder, encoder_optimizer, decoder_optimizer, criterion, emoTag, isTrain=True, max_length=MAX_LENGTH):

    encoder_hidden = encoder.initHidden()

    encoder_optimizer.zero_grad()
    decoder_optimizer.zero_grad()

    input_length = input_variable.size()[0]
    target_length = target_variable.size()[0]

    encoder_outputs = Variable(torch.zeros(max_length, encoder.hidden_dim))
    encoder_outputs = encoder_outputs.cuda() if use_cuda else encoder_outputs

    loss = 0

    for ei in range(input_length):
        encoder_output, encoder_hidden = encoder(
            input_variable[ei], encoder_hidden)
        encoder_outputs[ei] = encoder_output[0][0]

    decoder_input = target_variable[0]
    decoder_input = decoder_input.cuda() if use_cuda else decoder_input

    decoder_hidden = encoder_hidden

    use_teacher_forcing = True if random.random() < teacher_forcing_ratio else False

    if use_teacher_forcing:
        # Teacher forcing: Feed the target as the next input
        for di in range(1, target_length):
            decoder_output, decoder_hidden, decoder_attention = decoder(
                decoder_input, decoder_hidden, encoder_output, encoder_outputs, emoTag)
            loss += criterion(decoder_output, target_variable[di])
            decoder_input = target_variable[di]  # Teacher forcing

    else:
        # Without teacher forcing: use its own predictions as the next input
        for di in range(1, target_length):
            decoder_output, decoder_hidden, decoder_attention = decoder(
                decoder_input, decoder_hidden, encoder_output, encoder_outputs, emoTag)
            topv, topi = decoder_output.data.topk(1)
            ni = topi[0][0]

            decoder_input = Variable(torch.LongTensor([[ni]]))
            decoder_input = decoder_input.cuda() if use_cuda else decoder_input

            loss += criterion(decoder_output, target_variable[di])
            if ni == EOS_token:
                break

    if isTrain:
        loss.backward()
        encoder_optimizer.step()
        decoder_optimizer.step()

    return loss.data[0] / target_length


def trainIters(encoder, decoder, training_pairs, test_pairs=None, print_every=1000, test_every=1000, learning_rate=0.01):
    # start = time.time()
    train_losses = []
    test_losses = []
    print_loss_total = 0  # Reset every print_every
    plot_loss_total = 0  # Reset every plot_every

    encoder_optimizer = optim.SGD(encoder.parameters(), lr=learning_rate)
    decoder_optimizer = optim.SGD(decoder.parameters(), lr=learning_rate)
    criterion = nn.NLLLoss()

    iter = 0
    for training_pair in training_pairs:
        # training_pair: ((), emocls)
        emoTag = Variable(torch.LongTensor(training_pair[1]))
        emoTag = emoTag.cuda() if use_cuda else emoTag
        input_variable, target_variable = variablesFromPair(training_pair[0])
        input_variable = input_variable.cuda() if use_cuda else input_variable
        target_variable = target_variable.cuda() if use_cuda else target_variable
        loss = train(input_variable, target_variable, encoder,
                     decoder, encoder_optimizer, decoder_optimizer, criterion, emoTag)
        print_loss_total += loss
        plot_loss_total += loss
        iter += 1

        if iter % print_every == 0:
            print_loss_avg = print_loss_total / print_every
            print_loss_total = 0
            print "train loss: ", print_loss_avg
            train_losses.append(print_loss_avg)

            torch.save({
                'en': encoder.state_dict(),
                'de': decoder.state_dict(),
                'en_opt': encoder_optimizer.state_dict(),
                'de_opt': decoder_optimizer.state_dict(),
            }, './result/saved_model_lr001_dim500')
            # print('%s (%d %d%%) %.4f' % (timeSince(start, iter / n_iters), iter, iter / n_iters * 100, print_loss_avg))


        if test_pairs and iter % test_every == 0:
            test_loss = 0.0
            testIter = 0
            for test_pair in test_pairs:
                testIter += 1
                # training_pair: ((), emocls)
                emoTag = Variable(torch.LongTensor(test_pair[1]))
                emoTag = emoTag.cuda() if use_cuda else emoTag
                input_variable, target_variable = variablesFromPair(test_pair[0])
                loss = train(input_variable, target_variable, encoder,
                             decoder, encoder_optimizer, decoder_optimizer, criterion, emoTag, isTrain=False)
                test_loss += loss
            test_losses.append(test_loss / testIter)
            print "test loss: ", test_loss / testIter
            with open("./result/loss_lr001_dim500", 'w') as file:
                pickle.dump((train_losses, test_losses), file)

    return train_losses, test_losses


def showPlot(points):
    plt.figure()
    fig, ax = plt.subplots()
    # this locator puts ticks at regular intervals
    loc = ticker.MultipleLocator(base=0.2)
    ax.yaxis.set_major_locator(loc)
    plt.plot(points)


def main(wm, testWm, encoder, decoder, epoch, pathDir):
    trainLoss = []
    testLoss = []
    with open("./result/lookupTable_lr001_dim500", 'w') as file:
        pickle.dump(wm.lookupTable, file)
    for i in range(epoch):
        tmp = trainIters(encoder, decoder, wm.getBatch(), [j for j in testWm.getBatch()])
        trainLoss.extend(tmp[0])
        testLoss.extend(tmp[1])
        with open("./result/loss_lr001_dim500", 'w') as file:
            pickle.dump((trainLoss, testLoss), file)



if __name__ == "__main__":
    mood_dict = {
        0: 'joy',
        1: 'love',
        2: 'sadness',
        3: 'anger',
        4: 'fear',
        5: 'thankfulness',
        6: 'surprise'
    }
    epoch = 5
    emoIdx = {mood_dict[i]:i for i in mood_dict}
    emoCls = "../../data/Subtitles/subtitileData/smaller_emotion.txt"
    subtitle = "../../data/Subtitles/subtitileData/smaller.txt"
    test = "../../data/Subtitles/subtitileData/test.txt"
    testEmoCls = "../../data/Subtitles/subtitileData/test_emotion.txt"
    dm = DH.DataManager()
    wm = dm.buildModel(subtitle).buildLookupTabel().data4NN(subtitle, 1)
    wm.setEmotionCls(emoCls)
    wm.setEmoIdx(emoIdx)
    testWm = dm.data4NN(test, 1)
    testWm.setEmotionCls(testEmoCls)
    testWm.setEmoIdx(emoIdx)
    encoderInput_dim, encoderHidden_dim = 10000, 500
    decoderHidden_dim, decoderOutput_dim = 500, 10000
    embedding = nn.Embedding(encoderInput_dim, encoderHidden_dim)
    encoder = EncoderRNN(encoderInput_dim, encoderHidden_dim, embedding)
    decoder = AttnDecoderRNN(decoderHidden_dim, decoderOutput_dim, embedding)
    encoder = encoder.cuda() if use_cuda else encoder
    decoder = decoder.cuda() if use_cuda else decoder

    main(wm, testWm, encoder, decoder, epoch, pathDir='.')
