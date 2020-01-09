from sklearn import metrics
from torch import optim
from torch.nn.utils import clip_grad_norm_

from dataset import *
from network.model import *

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)-8s %(message)s')

DEVICE = 'cuda:0'
VALID_TIMES = 20


def train(config, log_path):
    """
    Before training, the input with '.json' format must be transformed into '.pt'
    format by 'data_prepare.py'. This process will also generate the 'vocab.pt'
    file which contains the basic statistics of the corpus.
    """
    log_f = open(log_path, 'a')

    if config.BAG_MODE:
        REModel = REModel_BAG
        DataLoader = DataLoader_BAG

    else:
        REModel = REModel_INS
        DataLoader = DataLoader_INS

    vocab = torch.load(os.path.join(config.ROOT_DIR, 'vocab.pt'))

    logging.info('Load pretrained vectors: {}*{}'.format(vocab.word_num, vocab.word_dim))
    logging.info('Number of classes: {}'.format(vocab.class_num))

    train_dataset = torch.load(os.path.join(config.ROOT_DIR, 'train.pt'))
    train_loader = DataLoader(train_dataset, config.BATCH_SIZE, collate_fn=train_dataset.collate, shuffle=True)

    valid_dataset = torch.load(os.path.join(config.ROOT_DIR, 'valid.pt'))
    valid_loader = DataLoader(valid_dataset, config.BATCH_SIZE, collate_fn=valid_dataset.collate, shuffle=False)

    logging.info('Number of train pair: {}'.format(len(train_dataset)))
    logging.info('Number of valid pair: {}'.format(len(valid_dataset)))

    model = REModel(vocab=vocab, tag_dim=config.TAG_DIM,
                    max_length=config.MAX_LENGTH,
                    hidden_dim=config.HIDDEN_DIM, dropout_prob=config.DROP_PROB,
                    bidirectional=config.BIDIRECTIONAL)

    if not config.EMBEDDING_FINE_TUNE:
        model.word_emb.weight.requires_grad = False

    logging.info('Using device {}'.format(DEVICE))

    model.to(DEVICE)
    model.display()

    weight = torch.FloatTensor(config.LOSS_WEIGHT) if config.LOSS_WEIGHT else None

    criterion = nn.CrossEntropyLoss(weight=weight, reduction='mean').to(DEVICE)

    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = optim.Adam(params, lr=config.LEARNING_RATE, weight_decay=config.L2_REG)

    validate_every = len(train_loader) // VALID_TIMES

    def run_iter(batch, is_training):
        model.train(is_training)

        sent = batch['sent'].to(DEVICE)
        tag = batch['tag'].to(DEVICE)
        pos1 = batch['pos1'].to(DEVICE)
        pos2 = batch['pos2'].to(DEVICE)
        length = batch['length'].to(DEVICE)

        label = batch['label'].to(DEVICE)
        id = batch['id']
        scope = batch['scope']

        logits = model(sent, tag, length)

        loss = criterion(input=logits, target=label)

        label_pred = logits.max(1)[1]

        if is_training:
            optimizer.zero_grad()
            loss.backward()
            clip_grad_norm_(parameters=params, max_norm=5)
            optimizer.step()

        return loss, label_pred.cpu()

    save_dir = os.path.join(config.SAVE_DIR, config.DATA_SET)
    if not os.path.isdir(save_dir):
        os.makedirs(save_dir)

    best_f1 = 0

    for epoch_num in range(config.MAX_EPOCHS):
        logging.info('Epoch {}: start'.format(epoch_num))

        train_labels = []
        train_preds = []

        for batch_iter, train_batch in enumerate(train_loader):
            train_loss, train_pred = run_iter(batch=train_batch, is_training=True)

            train_labels.extend(train_batch['label'])
            train_preds.extend(train_pred)

            if (batch_iter + 1) % validate_every == 0:

                torch.set_grad_enabled(False)

                valid_loss_sum = 0

                valid_labels = []
                valid_preds = []

                for valid_batch in valid_loader:
                    valid_loss, valid_pred = run_iter(batch=valid_batch, is_training=False)

                    valid_loss_sum += valid_loss.item()

                    valid_labels.extend(valid_batch['label'])
                    valid_preds.extend(valid_pred)

                torch.set_grad_enabled(True)

                valid_loss = valid_loss_sum / len(valid_loader)
                valid_p, valid_r, valid_f1, _ = metrics.precision_recall_fscore_support(valid_labels, valid_preds,
                                                                                        labels=[1, 2, 3, 4],
                                                                                        average='micro')

                train_f1 = metrics.f1_score(train_labels, train_preds, [1, 2, 3, 4], average='micro')

                progress = epoch_num + (batch_iter + 1) / len(train_loader)

                logging.info(
                    'Epoch {:.2f}: train loss = {:.4f}, train f1 = {:.4f}, valid loss = {:.4f}, valid f1 = {:.4f}'.format(
                        progress, train_loss, train_f1, valid_loss, valid_f1))

                if valid_f1 > best_f1:
                    best_f1 = valid_f1
                    model_filename = ('{}-{:.4f}.pkl'.format(config.DATA_SET, valid_f1))
                    model_path = os.path.join(save_dir, model_filename)
                    torch.save(model.state_dict(), model_path)
                    print('Saved the new best model to {}'.format(model_path))

                    log_f.write('{}\tlr={}\n'.format(model_filename, config.LEARNING_RATE))
                    log_f.flush()

    return best_f1


if __name__ == '__main__':
    from data.ddi import config

    for lr in range(1, 11):
        config.LEARNING_RATE = lr / 10000.0
        config.log()
        F = train(config, 'ddi.log')
