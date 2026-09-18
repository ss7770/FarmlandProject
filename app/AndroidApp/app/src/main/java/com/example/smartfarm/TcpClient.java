package com.example.smartfarm;

import android.os.Handler;
import android.os.Looper;
import android.util.Log;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.SocketTimeoutException;

public class TcpClient {
    private static final String TAG = "TcpClient";
    private static final int CONNECTION_TIMEOUT = 5000;
    private static final int READ_TIMEOUT = 10000;

    private Socket socket;
    private BufferedReader inputStream;
    private OutputStreamWriter outputStream;
    private String serverIp;
    private int serverPort;
    private boolean isConnected = false;
    private boolean isRunning = false;

    private OnConnectionListener connectionListener;
    private OnDataReceivedListener dataReceivedListener;
    private Handler handler = new Handler(Looper.getMainLooper());

    public interface OnConnectionListener {
        void onConnected();
        void onDisconnected();
        void onConnectionFailed(String error);
    }

    public interface OnDataReceivedListener {
        void onDataReceived(SensorData data);
    }

    public TcpClient(String serverIp, int serverPort) {
        this.serverIp = serverIp;
        this.serverPort = serverPort;
    }

    public void setConnectionListener(OnConnectionListener listener) {
        this.connectionListener = listener;
    }

    public void setDataReceivedListener(OnDataReceivedListener listener) {
        this.dataReceivedListener = listener;
    }

    public boolean isConnected() {
        return isConnected;
    }

    public void connect() {
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    socket = new Socket();
                    socket.connect(new InetSocketAddress(serverIp, serverPort), CONNECTION_TIMEOUT);
                    socket.setSoTimeout(READ_TIMEOUT);

                    inputStream = new BufferedReader(new InputStreamReader(socket.getInputStream()));
                    outputStream = new OutputStreamWriter(socket.getOutputStream());

                    isConnected = true;
                    isRunning = true;

                    notifyConnected();
                    startListening();

                } catch (SocketTimeoutException e) {
                    Log.e(TAG, "Connection timeout: " + e.getMessage());
                    notifyConnectionFailed("连接超时");
                } catch (IOException e) {
                    Log.e(TAG, "Connection failed: " + e.getMessage());
                    notifyConnectionFailed("连接失败: " + e.getMessage());
                }
            }
        }).start();
    }

    public void disconnect() {
        isRunning = false;
        isConnected = false;

        try {
            if (inputStream != null) {
                inputStream.close();
                inputStream = null;
            }
            if (outputStream != null) {
                outputStream.close();
                outputStream = null;
            }
            if (socket != null) {
                socket.close();
                socket = null;
            }
        } catch (IOException e) {
            Log.e(TAG, "Disconnect error: " + e.getMessage());
        }

        notifyDisconnected();
    }

    private void startListening() {
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    StringBuilder buffer = new StringBuilder();
                    String line;

                    while (isRunning && (line = inputStream.readLine()) != null) {
                        buffer.append(line);

                        if (line.contains("TEMP:") && line.contains("HUMI:") &&
                            line.contains("LIGHT:") && line.contains("SOIL:") &&
                            line.contains("WATER:")) {
                            final SensorData data = SensorData.parseFrom(buffer.toString());
                            Log.d(TAG, "Received data: " + data.toString());
                            notifyDataReceived(data);
                            buffer.setLength(0);
                        }
                    }
                } catch (SocketTimeoutException e) {
                    // Normal timeout, continue listening
                } catch (IOException e) {
                    Log.e(TAG, "Read error: " + e.getMessage());
                    disconnect();
                }
            }
        }).start();
    }

    public void sendThresholdCommand(int temp, int humi, int light, int soil, int water) {
        if (!isConnected || outputStream == null) {
            Log.w(TAG, "Not connected, cannot send command");
            return;
        }

        final String command = String.format("SETTHRESHOLD:TEMP:%d,HUMI:%d,LIGHT:%d,SOIL:%d,WATER:%d\r\n",
                temp, humi, light, soil, water);

        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    outputStream.write(command);
                    outputStream.flush();
                    Log.d(TAG, "Sent command: " + command);
                } catch (IOException e) {
                    Log.e(TAG, "Send error: " + e.getMessage());
                    disconnect();
                }
            }
        }).start();
    }

    private void notifyConnected() {
        handler.post(new Runnable() {
            @Override
            public void run() {
                if (connectionListener != null) {
                    connectionListener.onConnected();
                }
            }
        });
    }

    private void notifyDisconnected() {
        handler.post(new Runnable() {
            @Override
            public void run() {
                if (connectionListener != null) {
                    connectionListener.onDisconnected();
                }
            }
        });
    }

    private void notifyConnectionFailed(final String error) {
        handler.post(new Runnable() {
            @Override
            public void run() {
                if (connectionListener != null) {
                    connectionListener.onConnectionFailed(error);
                }
            }
        });
    }

    private void notifyDataReceived(final SensorData data) {
        handler.post(new Runnable() {
            @Override
            public void run() {
                if (dataReceivedListener != null) {
                    dataReceivedListener.onDataReceived(data);
                }
            }
        });
    }
}