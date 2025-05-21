from PyQt5.QtWidgets import *
from PyQt5.QtCore import QTimer, Qt
import pyqtgraph as pg
import numpy as np
from WF_SDK import device, scope, wavegen, error
from ctypes import *
import sys
from collections import deque
import os
from datetime import datetime
import winsound


# 初期値
amp_gain = 100  # アンプのゲイン v(t)=R*i(t)
avg_number = 1  # 平均数
daq_base_interval = 100 #ms
data_buffer_size = 1000  # 表示しているグラフのデータ数

class AD2Monitor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Tip Etcher: Chemical Etching Software for STM Probes')
        self.setGeometry(100, 100, 1200, 600)
        
        print("Program starting initialization...")

        # 初期設定値
        self.avg_window = avg_number
        self.base_interval = daq_base_interval
        self.is_pulsing = False
        self.is_logging = False
        self.log_file = None
        self.setup_logging_directory()
        
        # タイマーの初期化
        print("Connecting timers...")
        self.data_timer = QTimer(self)
        self.display_timer = QTimer(self)
        # pulse_timer は不要になったので削除
        
        # タイマーの接続
        self.data_timer.timeout.connect(self.acquire_and_average_data)
        self.display_timer.timeout.connect(self.update_graph_data)
        
        # データ取得の基本間隔を設定（100ms）
        self.base_data_interval = 100  # ミリ秒
        # ※ここでタイマーは開始しない

        try:
            # デバイスへの接続
            self.device_data = device.open()
            print(f"Connected to: {self.device_data.name}")

            self.hdwf = self.device_data.handle
            self.dwf = cdll.dwf
            
            # スコープの初期化
            scope.open(self.device_data)
            scope.trigger(self.device_data, enable=False)
            # スコープ初期化後に追加（__init__内）
            self.dwf.FDwfAnalogInChannelEnableSet(self.hdwf, c_int(0), c_bool(True))
            self.dwf.FDwfAnalogInChannelRangeSet(self.hdwf, c_int(0), c_double(10.0))  # CH1 の入力レンジを ±10V に

            
            # デジタルIOの初期化
            # 0番目ビットを出力として有効にする (0x0001)
            self.dwf.FDwfDigitalIOOutputEnableSet(self.hdwf, c_int(0x0001))
            self.dwf.FDwfDigitalIOOutputSet(self.hdwf, c_int(0))
            # 必要に応じてConfigureを呼び出す
            self.dwf.FDwfDigitalIOConfigure(self.hdwf)
            
            # 波形発生器の初期化（両チャンネル）
            wavegen.generate(
                self.device_data,
                channel=1,
                function=wavegen.function.dc,
                offset=0,
                amplitude=0
            )
            wavegen.generate(
                self.device_data,
                channel=2,
                function=wavegen.function.dc,
                offset=0,
                amplitude=0
            )
            
        except error as e:
            QMessageBox.critical(self, "Error", str(e))
            sys.exit(1)
            
        # データバッファの初期化
        self.display_buffer_size = data_buffer_size
        self.times = np.linspace(-self.display_buffer_size/100, 0, self.display_buffer_size)
        self.display_values = deque([0] * self.display_buffer_size, maxlen=self.display_buffer_size)
        self.voltage_buffer = []
        
        # UIのセットアップ
        self.setup_ui()
        
        # 初期設定のリフレッシュ
        self.avg_slider.setValue(self.avg_window)  # averaging の初期値を UI に反映
        self.avg_value_label.setText(f"Average Points: {self.avg_window}")
        # Y Scale の初期値を反映
        self.change_y_scale(self.y_scale_combo.currentText())
        # 平均用バッファの初期化
        self.acquisition_buffer = []

        # タイマーの開始（UIセットアップ後に一度だけ開始）
        print("Starting timers...")
        self.display_timer.start(40)
        self.data_timer.start(self.base_data_interval)
        print("Timers started")

    def setup_logging_directory(self):
        """ログディレクトリの設定"""
        self.log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'log')
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir)


    def acquire_and_average_data(self):
        """データの取得と指定回数ごとの平均化処理"""
        try:
            # 1. サンプルを取得（dataがリストの場合、1サンプル目を使用）
            data = scope.record(self.device_data, channel=1)
            sample = np.abs(data[0]) if isinstance(data, (list, tuple)) and len(data) > 0 else data

            # 2. サンプルをバッファに追加
            if not hasattr(self, 'acquisition_buffer'):
                self.acquisition_buffer = []
            self.acquisition_buffer.append(sample)

            # 3. バッファにavg_window個のサンプルがたまったら平均を計算
            if len(self.acquisition_buffer) >= self.avg_window:
                avg_value = sum(self.acquisition_buffer[:self.avg_window]) / self.avg_window
                self.acquisition_buffer = self.acquisition_buffer[self.avg_window:]
                # 4. 平均値を表示およびグラフ用バッファに追加
                self.current_avg_label.setText(f"Current Value: {avg_value:.3f} V")
                self.display_values.append(avg_value)

                #測定した電流値 (A) を計算
                measured_current = avg_value / amp_gain  # 単位: A
                try:
                    # Stop current 入力欄は mA なので、A に変換
                    threshold_current = float(self.stop_current_input.text()) * 0.001
                except ValueError:
                    threshold_current = 0.0  # 入力が無効な場合は 0 A とする

                # etching が動作中の場合のみ（例：Startボタンが無効になっている場合）
                if not self.etching_start_button.isEnabled() and measured_current < threshold_current:
                    print("Measured current ({:.3f} A) below threshold ({:.3f} A); stopping etching".format(measured_current, threshold_current))
                    self.stop_etching_process()

                # 5. ログが有効ならファイルに保存
                if self.is_logging and self.log_file:
                    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                    current_value = avg_value / amp_gain  # 電流値 (A) に換算
                    self.log_file.write(f"{timestamp},{current_value:.6f}\n")
                    self.log_file.flush()


        except error as e:
            print(f"Data acquisition error: {str(e)}")









    def update_graph_data(self):
        """表示の更新"""
        try:
            # 効果的なサンプル間隔（秒）
            effective_interval = (self.base_data_interval * self.avg_window) / 1000.0
            # データ点数分の時間軸を再計算
            self.times = np.linspace(-effective_interval * self.display_buffer_size, 0, self.display_buffer_size)
            
            scaled_values = np.array(list(self.display_values)) / amp_gain
            self.plot_curve.setData(self.times, scaled_values)
            self.plot_widget.setXRange(self.times[0], self.times[-1])
        except error as e:
            print(f"Display update error: {str(e)}")



    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        layout = QVBoxLayout(central_widget)
        layout.setSpacing(10)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # プロットウィジェットの設定
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground('w')
        self.plot_widget.showGrid(x=True, y=True)
        self.plot_widget.setLabel('left', 'Tip Current', units='A')
        self.plot_widget.setLabel('bottom', 'Time', units='s')
        self.plot_widget.setYRange(-5, 5)
        self.plot_widget.setXRange(-self.display_buffer_size/100, 0)
        self.plot_curve = self.plot_widget.plot(pen=pg.mkPen(color=(0, 0, 255), width=2))
        self.plot_widget.setMinimumHeight(300)
        layout.addWidget(self.plot_widget)
        
        # コントロールレイアウトの作成
        controls_layout = QHBoxLayout()
        controls_layout.addWidget(self.create_Yscale_group())
        controls_layout.addWidget(self.create_avg_group())
        controls_layout.addWidget(self.create_start_stop_group())
        controls_layout.addWidget(self.create_tip_voltage_group())
        controls_layout.addWidget(self.create_log_group())
        layout.addLayout(controls_layout)

    def create_Yscale_group(self):
        scale_group = QGroupBox("Y Scale")
        scale_layout = QVBoxLayout()        
        self.y_scale_combo = QComboBox()
        self.y_scale_combo.addItems(["0.5 mA/div", "1 mA/div", "2 mA/div", "5 mA/div", "10 mA/div", "20 mA/div", "50 mA/div"])
        self.y_scale_combo.setCurrentText("2 mA/div")
        self.y_scale_combo.currentTextChanged.connect(self.change_y_scale)
        scale_layout.addWidget(QLabel("Select Scale:"))
        scale_layout.addWidget(self.y_scale_combo)
        scale_group.setLayout(scale_layout)
        return scale_group

    def change_y_scale(self, text):
        try:
            mA_div = float(text.split()[0])
        except ValueError:
            return
        A_div = mA_div / 1000.0
        self.plot_widget.setYRange(-5 * A_div, 5 * A_div)

    def create_tip_voltage_group(self):
        tip_voltage_group = QGroupBox("Anode (tip) Voltage")
        tip_voltage_group.setMinimumWidth(250)
        layout = QVBoxLayout()
        
        # 周波数入力（Hz）
        freq_layout = QHBoxLayout()
        freq_layout.addWidget(QLabel("Frequency [Hz]:"))
        self.frequency_input = QLineEdit()
        self.frequency_input.setAlignment(Qt.AlignCenter)
        self.frequency_input.setText("1000")
        freq_layout.addWidget(self.frequency_input)
        layout.addLayout(freq_layout)
        
        # 振幅入力（V）
        amp_layout = QHBoxLayout()
        amp_layout.addWidget(QLabel("Amplitude [V]:"))
        self.amplitude_input = QLineEdit()
        self.amplitude_input.setAlignment(Qt.AlignCenter)
        self.amplitude_input.setText("1.0")
        amp_layout.addWidget(self.amplitude_input)
        layout.addLayout(amp_layout)
        
        # DCオフセット入力（V）
        offset_layout = QHBoxLayout()
        offset_layout.addWidget(QLabel("DC Offset [V]:"))
        self.dc_offset_input = QLineEdit()
        self.dc_offset_input.setAlignment(Qt.AlignCenter)
        self.dc_offset_input.setText("0")
        offset_layout.addWidget(self.dc_offset_input)
        layout.addLayout(offset_layout)
        
        tip_voltage_group.setLayout(layout)
        return tip_voltage_group

    def update_tip_voltage(self):
        try:
            frequency = float(self.frequency_input.text())
            amplitude = float(self.amplitude_input.text())
            dc_offset = float(self.dc_offset_input.text())
            # CH1 の出力を、入力値に基づいて更新（例：サイン波）
            wavegen.generate(
                self.device_data,
                channel=1,
                function=wavegen.function.sine,
                frequency=frequency,
                amplitude=amplitude,
                offset=dc_offset
            )
        except ValueError:
            QMessageBox.warning(self, "Error", "Invalid CH1 voltage settings.")

    def create_log_group(self):
        log_group = QGroupBox("Data Logging")
        log_group.setMinimumWidth(200)
        log_layout = QVBoxLayout()
        
        self.log_status_label = QLabel("Logging: OFF")
        self.log_status_label.setAlignment(Qt.AlignCenter)
               
        self.log_button = QPushButton("Start Logging")
        self.log_button.setCheckable(True)
        self.log_button.clicked.connect(self.toggle_logging)
        self.log_button.setStyleSheet("background-color: lightblue; color: white;")
        
        self.current_log_label = QLabel("Current log: None")
        self.current_log_label.setAlignment(Qt.AlignCenter)
        
        log_layout.addWidget(self.log_status_label)
        log_layout.addWidget(self.log_button)
        log_layout.addWidget(self.current_log_label)
        log_group.setLayout(log_layout)
        return log_group

    def create_avg_group(self):
        avg_group = QGroupBox("Average Control")
        avg_group.setMinimumWidth(200)
        avg_layout = QVBoxLayout()
        
        self.avg_slider = QSlider(Qt.Horizontal)
        self.avg_slider.setRange(1, 100)
        self.avg_slider.setValue(self.avg_window)
        self.avg_value_label = QLabel(f"Average Points: {self.avg_window}")
        self.avg_value_label.setAlignment(Qt.AlignCenter)
        
        self.current_avg_label = QLabel("Current Value: 0.000 V")
        self.current_avg_label.setAlignment(Qt.AlignCenter)
        font = self.current_avg_label.font()
        font.setPointSize(12)
        font.setBold(True)
        self.current_avg_label.setFont(font)
        
        self.avg_slider.valueChanged.connect(self.update_avg_window)
        avg_layout.addWidget(self.avg_value_label)
        avg_layout.addWidget(self.avg_slider)
        avg_layout.addWidget(self.current_avg_label)
        avg_group.setLayout(avg_layout)
        return avg_group

    def create_start_stop_group(self):
        """エッチング開始・停止のUIを作成：Stop current, Etching Start/Stopボタン"""
        start_stop_group = QGroupBox("Start / Stop")
        start_stop_group.setMinimumWidth(250)
        layout = QVBoxLayout()

        # Stop current 入力（mA）
        stop_current_layout = QHBoxLayout()
        stop_current_layout.addWidget(QLabel("Stop current [mA]:"))
        self.stop_current_input = QLineEdit()
        self.stop_current_input.setAlignment(Qt.AlignCenter)
        self.stop_current_input.setText("0")
        stop_current_layout.addWidget(self.stop_current_input)
        layout.addLayout(stop_current_layout)

        # ボタンレイアウト：Etching Start / Etching Stop
        button_layout = QHBoxLayout()
        self.etching_start_button = QPushButton("Start")
        self.etching_start_button.clicked.connect(self.start_etching)
        self.etching_start_button.setStyleSheet(
            "QPushButton { background-color: green; color: white; } "
            "QPushButton:disabled { background-color: lightgray; color: gray; }"
        )
        self.etching_stop_button = QPushButton("Stop")
        self.etching_stop_button.clicked.connect(self.stop_etching)
        self.etching_stop_button.setStyleSheet(
            "QPushButton { background-color: red; color: white; } "
            "QPushButton:disabled { background-color: lightgray; color: gray; }"
        )
        self.etching_start_button.setEnabled(True)
        self.etching_stop_button.setEnabled(False)
        
        button_layout.addWidget(self.etching_start_button)
        button_layout.addWidget(self.etching_stop_button)
        layout.addLayout(button_layout)

        start_stop_group.setLayout(layout)
        return start_stop_group

    def start_etching(self):
        #エッチング開始：ユーザ入力に基づいてCH1の出力を更新
        print("start_etching called")
        try:
            self.update_tip_voltage()
            self.etching_start_button.setEnabled(False)
            self.etching_stop_button.setEnabled(True)
            self.dwf.FDwfDigitalIOOutputSet(self.hdwf, c_int(0)) # DIOにゼロを送る
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to start etching: {str(e)}")

    def stop_etching_process(self):
        #エッチング停止プロセス：必要な後処理を行い、DIOの0番目をTrueに出力してからstop_etchingを呼び出す
        print("stop_etching_process called")
        # 0番目だけをONにする（他のビットを全てオフにする）のが目的なら単純に1をセット
        self.dwf.FDwfDigitalIOOutputSet(self.hdwf, c_int(1 << 0))
        
        # 変更を確定する
        self.dwf.FDwfDigitalIOConfigure(self.hdwf)

        # 5秒間、440Hzのビープ音を鳴らす (Windows専用)
        frequency = 3000   # 周波数[Hz]
        duration_ms = 3000  # 鳴らす時間[ミリ秒]
        winsound.Beep(frequency, duration_ms)

        # 最後にエッチング停止の処理を呼び出す
        self.stop_etching()


    def stop_etching(self):
        """エッチング停止：CH1 の出力をすべて 0V にする"""
        print("stop_etching called")
        try:
            wavegen.generate(
                self.device_data,
                channel=1,
                function=wavegen.function.dc,
                offset=0,
                amplitude=0
            )
            self.etching_start_button.setEnabled(True)
            self.etching_stop_button.setEnabled(False)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to stop etching: {str(e)}")

    def update_avg_window(self, value):
        self.avg_window = value
        self.avg_value_label.setText(f"Average Points: {value}")
        self.data_timer.setInterval(self.base_interval * value)

    def update_dac(self, voltage):
        try:
            self.dac_value_label.setText(f"{voltage:.2f} [V]")
            wavegen.generate(
                self.device_data,
                channel=1,
                function=wavegen.function.dc,
                offset=voltage
            )
        except error as e:
            print(f"DAC update error: {str(e)}")


    def toggle_logging(self, checked):
        if checked:
            try:
                # 入力された sampling rate (秒) に averaging 値を掛けた効果的なサンプリングレートにする
                #rate = float(self.log_rate_input.text())
                #if rate < 0.1:
                #    raise ValueError("Sampling rate must be >= 0.1 seconds")
                #effective_rate = rate * self.avg_window   # averaging 値を掛ける
                #interval_ms = int(effective_rate * 1000)

                interval_ms = self.base_data_interval * self.avg_window
                
                self.data_timer.stop()
                self.data_timer.setInterval(interval_ms)
                self.data_timer.start()
                
                timestamp = datetime.now().strftime('%y%m%d%H%M')
                log_filename = f"{timestamp}.log"
                self.log_file = open(os.path.join(self.log_dir, log_filename), 'w')
                self.log_file.write("Timestamp,Voltage\n")
                self.is_logging = True
                
                self.log_status_label.setText("Logging: ON")
                self.log_button.setText("Stop Logging")
                # ログボタンのスタイルを青色に（有効時も停止時も青）
                self.log_button.setStyleSheet("background-color: blue; color: white;")
                self.current_log_label.setText(f"Current log: {log_filename}")
            except ValueError as e:
                self.log_button.setChecked(False)
                QMessageBox.warning(self, "Error", str(e))
                return
        else:
            if self.log_file:
                self.log_file.close()
                self.log_file = None
            self.data_timer.stop()
            self.data_timer.setInterval(self.base_interval * self.avg_window)
            self.data_timer.start()
            self.is_logging = False
            self.log_status_label.setText("Logging: OFF")
            self.log_button.setText("Start Logging")
            # ログボタンのスタイルも青色に設定
            self.log_button.setStyleSheet("background-color: lightblue; color: white;")
            self.current_log_label.setText("Current log: None")


    def log_data(self):
        try:
            if self.is_logging and self.log_file:
                current_value = float(self.current_avg_label.text().split()[2])
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                self.log_file.write(f"{timestamp},{current_value:.6f}\n")
                self.log_file.flush()
        except Exception as e:
            print(f"Logging error: {str(e)}")

    def closeEvent(self, event):
        try:
            if hasattr(self, 'log_timer'):
                self.log_timer.stop()
            if self.is_logging and self.log_file:
                self.log_file.close()
            wavegen.generate(self.device_data, channel=1, function=wavegen.function.dc, offset=0)
            wavegen.generate(self.device_data, channel=2, function=wavegen.function.dc, offset=0)
            self.dwf.FDwfDigitalIOOutputSet(self.hdwf, c_int(0))
            scope.close(self.device_data)
            device.close(self.device_data)
        except error as e:
            print(f"Close error: {str(e)}")
        event.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setStyleSheet("""
        QMainWindow { background-color: #F0F0F0; }
        QGroupBox { font-weight: bold; border: 1px solid #CCCCCC; border-radius: 5px; margin-top: 1ex; padding: 10px; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 3px 0 3px; }
        QPushButton { padding: 5px; border-radius: 3px; }
        QLabel { padding: 2px; }
    """)
    window = AD2Monitor()
    window.show()
    sys.exit(app.exec_())
