# stm-tip-etching
STM探針を作製するためのPythonプログラムです。プログラムは予告なく変更する場合があります。

## 主な機能

- Pythonで書かれているので書き換え自由（オリジナルのソフトに作り変えてください、もちろん再配布も自由です）
- 自動ストップ（ビープ音もなる）
- DC+AC電圧の印加が可能
- 電流値のログ機能

## 必要なもの
- Analog Discovery 2 もしくは Analog Discovery 3
- 電流検出用の抵抗かアンプ
- 電圧印加用アンプ（100mAくらい流せるもの）

## 動作のしくみ
Analog Discoveryの信号発生器のCH1から電圧を出力しています。設定によって交流と直流を出せるようにしています。Analog Discovery単体では探針に流れる電流に限界があるかもしれないので、100mA程度を流すことが可能なアンプ（バッファ）を使用する必要があります。探針に流れる電流は何らかの方法で電圧に変換しなければなりません。例えば、単に抵抗に電流をながすか、トランスインピーダンス・アンプを使うことが考えられます。その電圧を入力CH1で測定しています。

自動的にストップする機能はDC電圧を印加している場合のみを想定しています。なにかアイデアがあれば議論したいです。

## プログラムの使い方
Windowsで動作確認済みです。MacやLinuxでも動作するかもしれません。Raspberry PiはARMアーキテクチャのため、公式のWaveFormsソフトウェアやドライバがそのまま動作するかわからないです。

プログラム（tip.py）の先頭にあるライブラリをあらかじめインストールしてください。プログラム内のamp_gainを使用しているアンプに合わせて値を変えてください。例えば、探針に流れる電流値i(t)を検出する場合電圧v(t)に変換する必要があります。通常抵抗を用いてv(t)=Ri(t)とする場合が多いので、それに対応させています。

