# Building-use labelling: Claude vs human gold

- gold buildings labelled: 173 (first pass); repeats: 0; human self-agreement: nan%
- recommended config (cheapest within 0.03 macro-F1 of best): **claude-haiku-4-5|text|label_v2**

## Per config

                         labeler   n  accuracy  macro_f1  abstain   ece  usd_per_1k
  claude-haiku-4-5|text|label_v2 173     0.393     0.188      0.0 0.363       3.566
 claude-sonnet-5-5|text|label_v2 173     0.434     0.163      0.0 0.283       9.013
  claude-opus-5-5|image|label_v1 173     0.370     0.160      0.0 0.156      17.014
   claude-opus-5-5|text|label_v2 173     0.422     0.160      0.0 0.226      18.095
   claude-opus-5-5|text|label_v1 173     0.387     0.159      0.0 0.164      16.232
 claude-sonnet-5-5|text|label_v1 173     0.376     0.148      0.0 0.249       8.141
claude-sonnet-5-5|image|label_v1 173     0.382     0.145      0.0 0.234       8.548
 claude-sonnet-5-5|text|label_v3 173     0.416     0.145      0.0 0.313       9.661
  claude-haiku-4-5|text|label_v1 173     0.289     0.143      0.0 0.401       3.130
  claude-haiku-4-5|text|label_v3 173     0.376     0.134      0.0 0.341       3.844
 claude-haiku-4-5|image|label_v1 173     0.272     0.121      0.0 0.398       3.337

## Diversity and use of context (all 400 gold candidates, gold labels not needed)

                         labeler   n  entropy  non_residential  unknown  cues_cited  %residential  %mixed_shop_house  %commercial  %institutional  %religious  %productive_use  %industrial_warehouse  %ancillary  %unknown
 claude-haiku-4-5|image|label_v1 400    0.475            0.470    0.060       3.788         0.470              0.002        0.000           0.032       0.002            0.000                  0.002       0.430     0.060
  claude-haiku-4-5|text|label_v1 400    0.471            0.450    0.055       3.770         0.495              0.002        0.002           0.035       0.002            0.000                  0.000       0.408     0.055
  claude-haiku-4-5|text|label_v2 400    0.392            0.332    0.002       5.420         0.665              0.002        0.015           0.032       0.002            0.000                  0.002       0.278     0.002
  claude-haiku-4-5|text|label_v3 400    0.442            0.305    0.018       5.378         0.678              0.018        0.012           0.040       0.002            0.002                  0.002       0.228     0.018
  claude-opus-5-5|image|label_v1 400    0.481            0.208    0.178       4.190         0.615              0.000        0.002           0.038       0.002            0.000                  0.000       0.165     0.178
   claude-opus-5-5|text|label_v1 400    0.444            0.165    0.170       4.245         0.665              0.000        0.002           0.032       0.002            0.000                  0.000       0.128     0.170
   claude-opus-5-5|text|label_v2 400    0.345            0.235    0.010       5.315         0.755              0.000        0.012           0.035       0.002            0.000                  0.000       0.185     0.010
claude-sonnet-5-5|image|label_v1 400    0.444            0.243    0.100       3.818         0.658              0.002        0.000           0.035       0.002            0.000                  0.000       0.202     0.100
 claude-sonnet-5-5|text|label_v1 400    0.435            0.205    0.110       3.812         0.685              0.000        0.002           0.032       0.002            0.000                  0.002       0.165     0.110
 claude-sonnet-5-5|text|label_v2 400    0.370            0.262    0.010       5.308         0.728              0.000        0.010           0.042       0.002            0.000                  0.002       0.205     0.010
 claude-sonnet-5-5|text|label_v3 400    0.324            0.225    0.005       5.088         0.770              0.000        0.010           0.035       0.002            0.000                  0.000       0.178     0.005

## By group and tagged

                         labeler         group  tagged  n  accuracy  macro_f1
 claude-haiku-4-5|image|label_v1 eastern_rural   False 52     0.288     0.102
 claude-haiku-4-5|image|label_v1  kigali_urban   False 33     0.273     0.094
 claude-haiku-4-5|image|label_v1  kigali_urban    True 11     0.455     0.436
 claude-haiku-4-5|image|label_v1  musanze_city   False 29     0.103     0.043
 claude-haiku-4-5|image|label_v1  musanze_city    True 13     0.615     0.317
 claude-haiku-4-5|image|label_v1      national   False 26     0.154     0.098
 claude-haiku-4-5|image|label_v1      national    True  9     0.333     0.250
  claude-haiku-4-5|text|label_v1 eastern_rural   False 52     0.308     0.106
  claude-haiku-4-5|text|label_v1  kigali_urban   False 33     0.242     0.084
  claude-haiku-4-5|text|label_v1  kigali_urban    True 11     0.455     0.436
  claude-haiku-4-5|text|label_v1  musanze_city   False 29     0.103     0.042
  claude-haiku-4-5|text|label_v1  musanze_city    True 13     0.615     0.317
  claude-haiku-4-5|text|label_v1      national   False 26     0.231     0.149
  claude-haiku-4-5|text|label_v1      national    True  9     0.444     0.383
  claude-haiku-4-5|text|label_v2 eastern_rural   False 52     0.404     0.128
  claude-haiku-4-5|text|label_v2  kigali_urban   False 33     0.545     0.175
  claude-haiku-4-5|text|label_v2  kigali_urban    True 11     0.455     0.394
  claude-haiku-4-5|text|label_v2  musanze_city   False 29     0.241     0.110
  claude-haiku-4-5|text|label_v2  musanze_city    True 13     0.538     0.239
  claude-haiku-4-5|text|label_v2      national   False 26     0.269     0.187
  claude-haiku-4-5|text|label_v2      national    True  9     0.333     0.250
  claude-haiku-4-5|text|label_v3 eastern_rural   False 52     0.442     0.149
  claude-haiku-4-5|text|label_v3  kigali_urban   False 33     0.455     0.150
  claude-haiku-4-5|text|label_v3  kigali_urban    True 11     0.455     0.394
  claude-haiku-4-5|text|label_v3  musanze_city   False 29     0.172     0.045
  claude-haiku-4-5|text|label_v3  musanze_city    True 13     0.538     0.239
  claude-haiku-4-5|text|label_v3      national   False 26     0.269     0.129
  claude-haiku-4-5|text|label_v3      national    True  9     0.333     0.250
  claude-opus-5-5|image|label_v1 eastern_rural   False 52     0.365     0.136
  claude-opus-5-5|image|label_v1  kigali_urban   False 33     0.455     0.183
  claude-opus-5-5|image|label_v1  kigali_urban    True 11     0.545     0.450
  claude-opus-5-5|image|label_v1  musanze_city   False 29     0.207     0.078
  claude-opus-5-5|image|label_v1  musanze_city    True 13     0.615     0.361
  claude-opus-5-5|image|label_v1      national   False 26     0.269     0.139
  claude-opus-5-5|image|label_v1      national    True  9     0.333     0.250
   claude-opus-5-5|text|label_v1 eastern_rural   False 52     0.404     0.141
   claude-opus-5-5|text|label_v1  kigali_urban   False 33     0.515     0.190
   claude-opus-5-5|text|label_v1  kigali_urban    True 11     0.455     0.478
   claude-opus-5-5|text|label_v1  musanze_city   False 29     0.207     0.052
   claude-opus-5-5|text|label_v1  musanze_city    True 13     0.538     0.239
   claude-opus-5-5|text|label_v1      national   False 26     0.269     0.127
   claude-opus-5-5|text|label_v1      national    True  9     0.444     0.383
   claude-opus-5-5|text|label_v2 eastern_rural   False 52     0.423     0.127
   claude-opus-5-5|text|label_v2  kigali_urban   False 33     0.576     0.175
   claude-opus-5-5|text|label_v2  kigali_urban    True 11     0.545     0.442
   claude-opus-5-5|text|label_v2  musanze_city   False 29     0.241     0.076
   claude-opus-5-5|text|label_v2  musanze_city    True 13     0.538     0.239
   claude-opus-5-5|text|label_v2      national   False 26     0.308     0.135
   claude-opus-5-5|text|label_v2      national    True  9     0.444     0.356
claude-sonnet-5-5|image|label_v1 eastern_rural   False 52     0.423     0.132
claude-sonnet-5-5|image|label_v1  kigali_urban   False 33     0.485     0.169
claude-sonnet-5-5|image|label_v1  kigali_urban    True 11     0.455     0.478
claude-sonnet-5-5|image|label_v1  musanze_city   False 29     0.207     0.050
claude-sonnet-5-5|image|label_v1  musanze_city    True 13     0.538     0.239
claude-sonnet-5-5|image|label_v1      national   False 26     0.231     0.111
claude-sonnet-5-5|image|label_v1      national    True  9     0.444     0.383
 claude-sonnet-5-5|text|label_v1 eastern_rural   False 52     0.404     0.137
 claude-sonnet-5-5|text|label_v1  kigali_urban   False 33     0.485     0.166
 claude-sonnet-5-5|text|label_v1  kigali_urban    True 11     0.455     0.336
 claude-sonnet-5-5|text|label_v1  musanze_city   False 29     0.172     0.042
 claude-sonnet-5-5|text|label_v1  musanze_city    True 13     0.462     0.195
 claude-sonnet-5-5|text|label_v1      national   False 26     0.346     0.200
 claude-sonnet-5-5|text|label_v1      national    True  9     0.333     0.222
 claude-sonnet-5-5|text|label_v2 eastern_rural   False 52     0.423     0.129
 claude-sonnet-5-5|text|label_v2  kigali_urban   False 33     0.576     0.195
 claude-sonnet-5-5|text|label_v2  kigali_urban    True 11     0.545     0.442
 claude-sonnet-5-5|text|label_v2  musanze_city   False 29     0.241     0.055
 claude-sonnet-5-5|text|label_v2  musanze_city    True 13     0.615     0.317
 claude-sonnet-5-5|text|label_v2      national   False 26     0.346     0.219
 claude-sonnet-5-5|text|label_v2      national    True  9     0.444     0.356
 claude-sonnet-5-5|text|label_v3 eastern_rural   False 52     0.442     0.134
 claude-sonnet-5-5|text|label_v3  kigali_urban   False 33     0.515     0.157
 claude-sonnet-5-5|text|label_v3  kigali_urban    True 11     0.545     0.442
 claude-sonnet-5-5|text|label_v3  musanze_city   False 29     0.241     0.055
 claude-sonnet-5-5|text|label_v3  musanze_city    True 13     0.538     0.239
 claude-sonnet-5-5|text|label_v3      national   False 26     0.308     0.144
 claude-sonnet-5-5|text|label_v3      national    True  9     0.444     0.242

## Gold class distribution

label
residential             67
mixed_shop_house        14
commercial              21
institutional           22
religious                2
productive_use          22
industrial_warehouse     9
ancillary               12
unknown                  4
