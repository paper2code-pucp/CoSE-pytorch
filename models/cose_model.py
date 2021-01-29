import json
import torch
import torch.nn as nn
import torch.nn.functional as F
import math
import numpy as np
from math import pi
from .gmm import *
from .transf_encoder_model import *
from .transf_decoder_model import *
from .seq2seq_encoder_model import *
from .relational_model import *
from utils import *
import wandb
from tqdm import tqdm
import os
torch.cuda.empty_cache()


class CoSEModel(nn.Module):
    def __init__(self,
            config_file,
            use_wandb=True
        ):
        super(CoSEModel, self).__init__()
    
        self.use_wandb = use_wandb

        if self.use_wandb:
            wandb.init(project="CoSE_Pytorch")
            wandb.watch_called = False

        self.config = configure_model(config_file, self.use_wandb)

        self.device = torch.device("cuda:2" if self.config.use_gpu and torch.cuda.is_available() else "cpu")
        self.encoder, self.decoder, self.position_predictive_model ,self.embedding_predictive_model = self.init_model(self.device, self.config, self.use_wandb)


    def tranform2image(self, strokes, seq_len, start_coords, mean_channel, std_channel, num_strokes, file_save_name):
        
        try:
            os.mkdir(self.config.root_path + self.config.diagrams_img_path)
        except OSError:
            pass
        
        
        npfig, fig, ax, file_save_path = transform_strokes_to_image(strokes, seq_len, start_coords, mean_channel,
                                                     std_channel, num_strokes, self.config.diagrams_img_path, file_save_name, square_figure=True, save=True, highlight_start=True)
        return npfig, fig, ax, file_save_path

    def test_strokes(self, valid_loader):
        mean_channel, std_channel = get_stats(self.config.stats_path)
        
        num_batch = 0

        list_names_files = []
        recon_cd = None
        pred_cd = None

        for batch_input, batch_target in iter(valid_loader):

            num_batch = num_batch + 1
            encoder_inputs = batch_input['encoder_inputs'].squeeze(dim = 0)
            num_strokes = batch_input['num_strokes'].squeeze(dim = 0)
            seq_len_drawing = batch_input['seq_len'].squeeze(dim = 0)
            start_coord = batch_input['start_coord'].squeeze(dim = 0).squeeze()

            enc_inputs = encoder_inputs.reshape(-1, num_strokes.max(), encoder_inputs.size(1), encoder_inputs.size(2))
            seq_len = seq_len_drawing.reshape(-1, num_strokes.max())
            st_coord = start_coord.reshape(-1,num_strokes.max(), start_coord.size(1))

            #forward autoregressive


            #calculate recon_cd and pred_cd

            #save diagramas in jpg for the first batch
            if num_batch==1:
                num_diagrams = enc_inputs.shape[0]
                #save image
                for i_diagram in range(num_diagrams):
                    npfig, fig, _, file_save_path = tranform2image(enc_inputs[i_diagram], seq_len[i_diagram], st_coord[i_diagram], mean_channel, std_channel, num_strokes[i_diagram], file_save_name="diagrama_n_{}".format(i_diagram))
                    list_names_files.append(file_save_path)


        return (recon_cd, pred_cd, list_names_files)



    def forward(self, diagrama):
        #diagram (1, ...)
        _, look_ahead_mask, _ = generate_3d_mask(encoder_inputs, strok_len_inputs,self.device)
        encoder_out = cose.encoder(encoder_inputs.permute(1,0,2), strok_len_inputs, look_ahead_mask)
        diagram_embedding, padded_max_num_strokes, _, num_diagrams = reshape_stroke2diagram(encoder_out,num_strokes)
        start_pos_base = start_coord.reshape(num_diagrams,padded_max_num_strokes,2)
        pos_model_inputs = torch.cat([diagram_embedding, start_pos_base], dim = 2)
        pos_pred_mu, pos_pred_sigma, pos_pred_pi = cose.position_predictive_model(pos_model_inputs, num_strokes, None)
        pos_model_output = cose.position_predictive_model.draw_sample(pos_pred_mu, pos_pred_sigma, pos_pred_pi)
        pred_model_inputs = torch.cat([diagram_embedding, start_pos_base, pos_model_output.unsqueeze(dim = 1).repeat(1, diagram_embedding.size(1), 1)],
                                      dim = 2)
        emb_pred_mu, emb_pred_sigma, emb_pred_pi = cose.embedding_predictive_model(pred_model_inputs, num_strokes, None)
        strokes_output = cose.embedding_predictive_model.draw_sample(emb_pred_mu, emb_pred_sigma, emb_pred_pi)
        out = self.encoder(diagrama, stroke_lengths, src_mask)
        out = self.position_predictive_model(out)
        out = self.embedding_predictive_model(out)
        out = self.decoder(out)
        stroke_image = self.tranform2image(out)
        return out



    def init_model(self, device, config, use_wandb=True):

        if config.ae_model_type == "transformer":
            encoder = Trans_encoder(d_model=self.config.enc_d_model, nhead=self.config.enc_nhead, dff=self.config.enc_dff,
                            nlayers=self.config.enc_n_layers, size_embedding=self.config.size_embedding, dropout=self.config.enc_dropout)
                        
            decoder = Trans_decoder(size_embedding=self.config.size_embedding, num_components=self.config.dec_gmm_num_components,
                            out_units=2, layer_features=self.config.dec_layer_features)
        

        elif config.ae_model_type == "rnn":
            encoder = EncoderRNN(input_size=3, hidden_size=self.config.enc_hsize, 
                                encoder_dim=self.config.size_embedding, num_layers=self.config.enc_n_layers, device=self.device, dropout= self.config.enc_dropout)

            decoder = DecoderRNN(hidden_size=self.config.dec_hsize, t_input_size=4, output_size=16, 
                            encoder_dim=self.config.size_embedding + 1, num_layers=self.config.dec_n_layers, device=self.device, 
                            dim_layer=self.config.dec_dim_layer, num_components=self.config.dec_gmm_num_components, dropout = self.config.dec_dropout)
        
        
        position_predictive_model = TransformerGMM(d_model=self.config.rel_d_model,nhead=self.config.rel_nhead,
                                                   dff=self.config.rel_dff, nlayers=self.config.rel_n_layers,
                                                   input_size= self.config.size_embedding + 2,
                                                   num_components= self.config.rel_gmm_num_components,
                                                   out_units = 2, dropout = self.config.rel_dropout
                                                  )

        embedding_predictive_model = TransformerGMM(d_model = self.config.rel_d_model, nhead = self.config.rel_nhead,
                                                    dff = self.config.rel_dff, nlayers = self.config.rel_n_layers,
                                                    input_size= self.config.size_embedding + 4,
                                                    num_components = self.config.rel_gmm_num_components,
                                                    out_units = self.config.size_embedding, dropout = self.config.rel_dropout
                                                  )

        encoder.to(device)
        decoder.to(device)
        position_predictive_model.to(device)
        embedding_predictive_model.to(device)

        if use_wandb:
            wandb.watch(encoder, log="all")
            wandb.watch(decoder, log="all")
            wandb.watch(position_predictive_model, log="all")
            wandb.watch(embedding_predictive_model, log="all")

        return (encoder, decoder, position_predictive_model, embedding_predictive_model)



    def init_optimizers(self):
        list_autoencoder = list(self.encoder.parameters()) + list(self.decoder.parameters())
        optimizer_ae = torch.optim.Adam(list_autoencoder, lr=self.config.lr_ae)

        list_pos_pred = list(self.position_predictive_model.parameters())
        optimizer_pos_pred = torch.optim.Adam(list_pos_pred, lr=self.config.lr_pos_pred)

        list_emb_pred = list(self.embedding_predictive_model.parameters())
        optimizer_emb_pred = torch.optim.Adam(list_emb_pred, lr=self.config.lr_emb_pred)

        
        return (optimizer_ae, optimizer_pos_pred, optimizer_emb_pred)


    def train_step(self, train_loader, optimizers):

        optimizer_ae, optimizer_pos_pred, optimizer_emb_pred = optimizers    

        self.encoder.train()
        self.decoder.train()
        self.embedding_predictive_model.train()
        self.position_predictive_model.train()

        for batch_input, batch_target in iter(train_loader):

            self.encoder.zero_grad()
            self.decoder.zero_grad()
            self.embedding_predictive_model.zero_grad()
            self.position_predictive_model.zero_grad()
            
            # Parsing inputs
            enc_inputs, t_inputs, stroke_len_inputs, inputs_start_coord, inputs_end_coord, num_strokes_x_diagram_tensor = parse_inputs(batch_input,self.device)
            t_target_ink = parse_targets(batch_target,self.device)
            # Creating sequence length mask
            _, look_ahead_mask, _ = generate_3d_mask(enc_inputs, stroke_len_inputs, self.device)
            # Encoder forward
            encoder_out = self.encoder(enc_inputs.permute(1,0,2), stroke_len_inputs, look_ahead_mask)
            # decoder forward
            encoder_out_reshaped = encoder_out.unsqueeze(dim=1).repeat(1,t_inputs.shape[1],1).reshape(-1, encoder_out.shape[-1])
            t_inputs_reshaped = t_inputs.reshape(-1,1)
            decoder_inp = torch.cat([encoder_out_reshaped, t_inputs_reshaped], dim = 1)
            strokes_out, ae_mu, ae_sigma, ae_pi= self.decoder(decoder_inp)
            
            # Random/Ordered Sampling
            pred_inputs, pred_input_seq_len, context_pos, pred_targets, target_pos = random_index_sampling(encoder_out,inputs_start_coord,
                                                                                            inputs_end_coord,num_strokes_x_diagram_tensor,
                                                                                            input_type =self.config.input_type,
                                                                                            num_predictive_inputs = self.config.num_predictive_inputs,
                                                                                            replace_padding = self.config.replace_padding,
                                                                                            end_positions = self.config.end_positions
                                                                                            , device = self.device)
            # Detaching gradients of pred_targets (Teacher forcing)
            if self.config.stop_predictive_grad:
                pred_inputs = pred_inputs.detach()
                pred_input_seq_len = pred_input_seq_len.detach()
                context_pos = context_pos.detach()
                pred_targets = pred_targets.detach()
                target_pos = target_pos.detach() #Detaching gradients of pred_inputs (No influence of Relational Model)
            # Concatenating inputs for relational model
            pos_model_inputs = torch.cat([pred_inputs, context_pos], dim = 2)
            pred_model_inputs = torch.cat([pred_inputs, context_pos, target_pos.unsqueeze(dim = 1).repeat(1, pred_inputs.shape[1], 1)], dim = 2)
            # Predictive model Teacher forcing
            emb_pred_mu, emb_pred_sigma, emb_pred_pi = self.embedding_predictive_model(pred_model_inputs, pred_input_seq_len.int(), None)
            # Position model 
            pos_pred_mu, pos_pred_sigma, pos_pred_pi = self.position_predictive_model(pos_model_inputs, pred_input_seq_len.int(), None)
            
            loss_ae = -1*(logli_gmm_logsumexp(t_target_ink, ae_mu, ae_sigma, ae_pi).sum())
            loss_pos_pred = -1*(logli_gmm_logsumexp(target_pos, pos_pred_mu, pos_pred_sigma, pos_pred_pi).sum())
            loss_emb_pred = -1*(logli_gmm_logsumexp(pred_targets, emb_pred_mu, emb_pred_sigma, emb_pred_pi).sum())
            
            loss_total = loss_pos_pred + loss_emb_pred + loss_ae
            #sys.exit(0)
            loss_total.backward()

            optimizer_pos_pred.step()
            optimizer_emb_pred.step()
            optimizer_ae.step()

        return (loss_ae, loss_pos_pred, loss_emb_pred, loss_total)


    def save_weights(self, path_gen, path_sub, use_wandb=True):

        torch.save(self.encoder.state_dict(), os.path.join(path_sub, 'encoder.pth'))
        torch.save(self.decoder.state_dict(), os.path.join(path_sub, 'decoder.pth'))
        torch.save(self.embedding_predictive_model.state_dict(), os.path.join(path_sub, 'emb_pred.pth'))
        torch.save(self.position_predictive_model.state_dict(), os.path.join(path_sub, 'pos_pred.pth'))

        if use_wandb:
            wandb.save(os.path.join(path_sub,'*.pth'),base_path='/'.join(path_gen.split('/')[:-2]))


    def load_weights(self):
        self.encoder.load_state_dict(
            torch.load(self.config.model_path + 'encoder.pth',map_location=torch.device(self.device)))

        self.decoder.load_state_dict(
            torch.load(self.config.model_path + 'decoder.pth',map_location=torch.device(self.device)))

        self.embedding_predictive_model.load_state_dict(
            torch.load(self.config.model_path + 'emb_pred.pth',map_location=torch.device(self.device)))

        self.position_predictive_model.load_state_dict(
            torch.load(self.config.model_path + 'pos_pred.pth',map_location=torch.device(self.device)))


    def fit(self):
                        
        if self.config.use_gpu and torch.cuda.is_available():
            print("Training in " + torch.cuda.get_device_name(0))  
        else:
            print("Training in CPU")

        if self.config.save_weights:
            path_save_weights = self.config.root_path + self.config.save_path
        try:
            os.mkdir(path_save_weights)
        except OSError:
            pass

        #optimizer_ae, optimizer_pos_pred, optimizer_emb_pred 
        optimizers = self.init_optimizers()
    

        train_loader = get_batch_iterator(self.config.train_dataset_path)
        valid_loader = get_batch_iterator(self.validation_dataset_path)

        for epoch in tqdm(range(self.config.num_epochs)):
            loss_ae, loss_pos_pred, loss_emb_pred, loss_total = self.train_step(train_loader, optimizers)
            #TODO valid_loader shape: (n_ejemplos, num_strokesxdiagrama, num_puntos, 2)
            recon_cd, pred_cd, list_names_files = test_strokes(valid_loader)
            
            if self.use_wandb:
                wandb.log({"train_epoch":epoch+1,
                            "Generated strokes": [wandb.Image(img) for img in list_names_files],
                            "recon_chamfer_distance": recon_cd,
                            "pred_chamfer_distance": pred_cd,
                            "loss_ae":loss_ae.item(),
                            "loss_pos_pred":loss_pos_pred.item(),
                            "loss_emb_pred":loss_emb_pred.item(), 
                            "loss_total":loss_total.item()})

            if self.config.save_weights and ((epoch+1)% int(self.config.num_epochs/self.config.num_backups))==0:
                path_save_epoch = path_save_weights + 'epoch_{}'.format(epoch+1)
                
                try:
                    os.mkdir(path_save_epoch)
                except OSError:
                    pass

                self.save_weights(path_save_weights, path_save_epoch, self.use_wandb)

            print("Losses")
            print('Epoch [{}/{}], Loss autoencoder: {:.4f}'.format(epoch+1, self.config.num_epochs, loss_ae.item()))
            print('Epoch [{}/{}], Loss position prediction: {:.4f}'.format(epoch+1, self.config.num_epochs, loss_pos_pred.item()))
            print('Epoch [{}/{}], Loss embedding prediction: {:.4f}'.format(epoch+1, self.config.num_epochs, loss_emb_pred.item()))
            print('Epoch [{}/{}], Loss total: {:.4f}'.format(epoch+1, self.config.num_epochs, loss_total.item()))
        
        if self.use_wandb:
            wandb.finish()