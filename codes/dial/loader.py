import os
import json
import torch
from pdb import set_trace
from itertools import chain
from transformers import cached_path
from torch.utils.data import DataLoader
from torch.utils.data.distributed import DistributedSampler
from torch.nn.utils.rnn import pad_sequence


##############################################################################
# Info： Tokenize and encode the dataset. 
##############################################################################
def encode_data(tokenizer, data_path, data_cache):
    """Cache the processed data when first tokenizing for quick use."""
    data_cache = data_cache + "_" + type(tokenizer).__name__
    if data_cache and os.path.isfile(data_cache):
        dataset = torch.load(data_cache)
        pass
    else:
        print("Process dataset from {:s}".format(data_path))
        plan_file = cached_path(data_path)
        with open(plan_file, "r", encoding="utf-8") as f:
            dataset = []
            dialogs = json.loads(f.read())
            
            for dialog in dialogs: 
                dial_hist = []
                for turn in dialog:
                    utter = turn["utter"]
                    dial_hist.extend([utter])
                    """Build sample when the response generated by Wizard (bot)."""
                    if turn["role"] == "bot" and len(dial_hist) > 1:
                        # dial_enc = [tokenizer.convert_tokens_to_ids(tokenizer.tokenize(sent.strip())) + 
                        #             tokenizer.tokenize("\n") for sent in dial_hist]
                        # klg_enc = [tokenizer.convert_tokens_to_ids(tokenizer.tokenize(klg.strip())) + 
                        #         tokenizer.tokenize("\n") for klg in turn["klg"].values()]
                        dial_enc = [tokenizer.encode(sent) for sent in dial_hist]
                        if isinstance(turn["klg"], dict):
                            klg_enc = [tokenizer.encode(klg) for klg in turn["klg"].values()]
                            """Only the reply that have a dependent external knowledge can be built."""
                            if "no_passages_used" in turn["klg"].keys():
                                continue
                            if len(klg_enc) == 0:
                                continue
                        else:
                            klg_enc = [tokenizer.encode(turn["klg"])]
                            if len(klg_enc) == 0:
                                continue
                            pass
                                                
                        new_data = {
                            "dial_enc": dial_enc,
                            "klg_enc": klg_enc
                        }
                        dataset.append(new_data)
                        pass
                    pass
                pass
            pass
        
        print("Tokenize and encode the dataset.")
        torch.save(dataset, data_cache)
        pass

    return dataset


##############################################################################
# Info： Generate a batch with three types.
##############################################################################
def get_batch(batch_next, tokenizer, training=False, seq_len=512, device="cuda"):
    input_ids, token_ids, label_ids, klg_ids = batch_next
    padding, pad_token = tokenizer.pad_token_id, 3
    input_list, token_list, label_list, pos_list, mask_list = [], [], [], [], []
    
    if training:
        """Make loss_mask metric."""
        for batch_id in range(input_ids.size(0)):
            """Get each input, label and token in batch."""
            input_id = input_ids[batch_id]
            label_id = label_ids[batch_id]
            token_id = token_ids[batch_id]
            
            """Delete padding."""
            mask_input = torch.ne(input_id, padding)
            mask_label = torch.ne(label_id, padding)
            mask_token = torch.ne(token_id, pad_token)
            
            new_input = torch.masked_select(input_id, mask_input)
            new_label = torch.masked_select(label_id, mask_label)
            new_token = torch.masked_select(token_id, mask_token)

            inp_len = new_input.size()[0]

            """Build output labels in two ways:"""
            new_label = torch.cat([torch.tensor([tokenizer.pad_token_id] * (inp_len - (new_label.size()[0]))), new_label], dim=-1)

            # print(new_input.size(), new_token.size(), new_label.size())
            assert new_input.size() == new_token.size() == new_label.size()
            
            padding_size = int(seq_len) - inp_len
            
            input_pad = torch.tensor([padding] * padding_size)
            label_pad = torch.tensor([padding] * padding_size)
            token_pad = torch.tensor([pad_token] * padding_size)
            
            """Shift left in loss calculation."""
            final_input = torch.cat([new_input, input_pad], dim=-1)
            final_token = torch.cat([new_token, token_pad], dim=-1)
            final_label = torch.cat([new_label, label_pad], dim=-1) 

            """length - 1 since label shift."""
            final_loss_mask = torch.ones(final_label.size(0) - 1, dtype=torch.float)
            
            """1. Only calculate the loss of response part;"""
            final_loss_mask = torch.masked_fill(final_loss_mask, torch.eq(final_label[..., 1:].contiguous(), tokenizer.pad_token_id), 0.0)
            """2. Calculate the whole sentences."""
            # final_loss_mask = torch.masked_fill(final_loss_mask, torch.eq(final_input[..., 1:].contiguous(), tokenizer.pad_token_id), 0.0)

            final_pos = torch.arange(len(final_input), dtype=torch.long)

            input_list.append(final_input)
            token_list.append(final_token)
            label_list.append(final_label)
            pos_list.append(final_pos)
            mask_list.append(final_loss_mask)
            pass
        
        """Put all tensor items to device."""
        # set_trace()
        input_ids = torch.stack(input_list).type(torch.long).to(torch.device(device))
        token_ids = torch.stack(token_list).type(torch.long).to(torch.device(device))
        label_ids = torch.stack(label_list).type(torch.long).to(torch.device(device))
        loss_mask = torch.stack(mask_list).type(torch.long).to(torch.device(device))
        # loss_mask = torch.stack(mask_list).type(torch.long).to(torch.device(device))
        pos_ids = torch.stack(pos_list).type(torch.long).to(torch.device(device))
        pass
    else:
        """When evaluating, the sentence is inputted one-by-one in turn."""
        final_loss_mask = torch.ones(label_ids.size(1) - 1, dtype=torch.float)
        loss_mask = torch.masked_fill(final_loss_mask, torch.eq(label_ids[..., 1:].contiguous(), tokenizer.pad_token_id), 0.0)
        
        final_pos = torch.arange(label_ids.size(1), dtype=torch.long)
        pos_ids = final_pos.squeeze(0)
        
        """Put all tensor items to device."""
        input_ids = input_ids.to(torch.device(device))
        token_ids = token_ids.to(torch.device(device))
        label_ids = label_ids.to(torch.device(device))
        loss_mask = loss_mask.to(torch.device(device))
        # loss_mask = torch.stack(mask_list)
        pos_ids = pos_ids.to(torch.device(device))
        pass
        
    return  input_ids, token_ids, pos_ids, label_ids, klg_ids, loss_mask


##############################################################################
# Info： Make training data of dialogues containing "context", "response" and 
#        "knowledge" for training process. 
##############################################################################
class WikipediaDataset():
    def __init__(self, tokenizer, seq_len=512, data_path=None, data_cache=None, batch_first=True, is_label=True):
        self.tokenizer = tokenizer
        self.pad = tokenizer.pad_token_id
        self.bos = self.tokenizer.bos_token_id
        self.eos = self.tokenizer.eos_token_id
        self.seq_len = seq_len
        self.batch_first = batch_first
        self.is_label = is_label

        """Different separate token and token type ids."""
        self.bot, self.user = self.tokenizer.eos_token_id, self.tokenizer.eos_token_id
        self.bot_st, self.user_st, self.klg_st, self.pad_st = 0, 1, 2, 3
        
        """Set ratio to 0.001 for low-resource setting and quickly debug."""
        self.ratio = 1
        self.cnt = 0
        
        """Preprocess the tokenized conversations."""
        self.convers = encode_data(tokenizer, data_path, data_cache)
        self.data = []
        
        for conv in self.convers[:int(self.ratio * len(self.convers))]:
            dial = conv["dial_enc"]
            klg = conv["klg_enc"]
            ins = self.instances(dial, klg, is_label=self.is_label)
            
            if len(ins["input_ids"]) <= seq_len:
                self.data.append(ins)
                pass
            else:
                continue
            pass
        pass
        
    def instances(self, dial, klgs, is_label=False):        
        """Split dialogue history and response."""
        hist, reply = dial[:-1], dial[-1]
        
        """history form: [[SEP], u1, [SEP], b1, [SEP], u2, [SEP], b2, ..., [SEP], bn]."""
        hist = [[self.user if i % 2 == 0 else self.bot] + s for i, s in enumerate(hist)]
        hist_tti = [[self.user_st] * len(s) if i % 2 == 0 else [self.bot_st] * len(s) for i, s in enumerate(hist)]
        
        """knowledge form: [[BOS], gold knowledge]."""
        klg = [[] + s for _, s in enumerate(klgs)]
        klg_tti = [[self.klg_st] * len(s) for _, s in enumerate(klgs)]
        
        """ "+1" for [EOS] token."""
        reply = [self.bot] + reply
        reply_tti = [self.bot_st] * (len(reply) + 1)
        
        """Make sure the splited sequence length less than self.seq_len."""
        """1. The input sequence is built as: [BOS] + Knowledge + [SEP] + Dialogue Contexts + [SEP] + Response."""
        seq_len = len(list(chain(*hist)) + list(chain(*klg)) + reply) + 2
        hist_len = self.seq_len - len(list(chain(*klg)) + reply) - 2
        if seq_len <= self.seq_len:
            inputs = [[self.bos]] + [list(chain(*klg))] + [list(chain(*hist))] + [reply + [self.eos]]
            input_tti = [klg_tti[0][0]] + list(chain(*klg_tti)) + list(chain(*hist_tti)) + reply_tti
        else:
            # set_trace()
            inputs = [[self.bos]] + [list(chain(*klg))] + [list(chain(*hist))[-hist_len:]] + [reply + [self.eos]]
            input_tti = [klg_tti[0][0]] + list(chain(*klg_tti)) + list(chain(*hist_tti))[-hist_len:] + reply_tti
            self.cnt += 1
        
        """2. Remove knowledge"""
        # seq_len = len(list(chain(*hist)) + reply) + 2
        # hist_len = self.seq_len - 2
        # if seq_len <= self.seq_len:
        #     inputs = [[self.bos]] + [list(chain(*hist))] + [reply + [self.eos]]
        #     input_tti = [hist_tti[0][0]] + list(chain(*hist_tti)) + reply_tti
        # else:
        #     # set_trace()
        #     inputs = [[self.bos]] + [list(chain(*hist))[-hist_len:]] + [reply + [self.eos]]
        #     input_tti = [klg_tti[0][0]] + list(chain(*hist_tti))[-hist_len:] + reply_tti
        #     self.cnt += 1

        
        ins = {
            "input_ids": list(chain(*inputs)),
            "token_type_ids": input_tti,
            "knowledge_ids": list(chain(*klg))
        }

        if is_label:
            """1. The label sequence is built as: [PAD] + ... + Response + [EOS]."""
            # ins["lm_label"] = [self.pad] * (len(ins["input_ids"]) - len(reply) - 1) + reply + [self.eos]
            """2. Remove knowledge"""
            ins["lm_label"] = [self.pad] * (len(ins["input_ids"]) - len(reply) - 1) + reply + [self.eos]
            pass
        
        """Make sure the sequences are equal length."""
        # print(len(ins["input_ids"]), len(ins["token_type_ids"]), len(ins["lm_label"]))
        assert len(ins["input_ids"]) == len(ins["token_type_ids"]) == len(ins["lm_label"])

        return ins
    
    def collate(self, batch):
        input_ids = pad_sequence([torch.tensor(ins["input_ids"], dtype=torch.long) for ins in batch], 
                                 batch_first=self.batch_first, padding_value=self.pad)
        token_ids = pad_sequence([torch.tensor(ins["token_type_ids"], dtype=torch.long) for ins in batch], 
                                      batch_first=self.batch_first, padding_value=self.pad_st)
        label_ids = pad_sequence([torch.tensor(ins["lm_label"], dtype=torch.long) for ins in batch], 
                                batch_first=self.batch_first, padding_value=self.pad)
        klg_ids = pad_sequence([torch.tensor(ins["knowledge_ids"], dtype=torch.long) for ins in batch], 
                                batch_first=self.batch_first, padding_value=self.pad)

        return input_ids, token_ids, label_ids, klg_ids

    def __len__(self):
        return len(self.data)

    def __getitem__(self, index):
        return self.data[index]


##############################################################################
# Info： Build training, validation and test data loaders.. 
##############################################################################
def build_loaders(args, tokenizer, logger):
    logger.info("Build training, validation and test data loaders")

    data_loaders = []
    
    if args.stage == "train":
        sets_type = ["train", "valid"]
        pass
    elif args.stage == "infer":
        sets_type = ["test"]
        pass
    else:
        raise Exception('Unknown dataset type to load.')
        
    for set_type in sets_type:
        if args.global_rank == 0:
            print("Load tokenized dataset from cache at {:s}".format(os.path.join(args.cache_path, set_type + '_cache')))
            
        dataset = WikipediaDataset(tokenizer, seq_len=args.seq_len, data_path=os.path.join(args.data_path, set_type + '.json'), 
                                    data_cache=os.path.join(args.cache_path, set_type + '_cache'))
        
        if args.global_rank == 0:
            print("Number of " + set_type + " samples: {:d}, and {:d} over max_seq were splited.".format(len(dataset), dataset.cnt))
        
        """If args.distributed else None."""
        data_sampler = DistributedSampler(dataset, shuffle=True, drop_last=True)
    
        data_loaders.append(DataLoader(dataset,
                            collate_fn=dataset.collate,
                            pin_memory=(args.device == "cuda"),
                            num_workers=1,
                            sampler=data_sampler,
                            batch_size=args.batch_size if set_type=="train" else 1,
                            shuffle=False))
        pass

    return data_loaders

