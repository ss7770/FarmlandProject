package com.example.smartfarm;

/**
 * 宿主接口（2026-10-05 加底部 Tab 栏时引入）。
 *
 * <p>为什么需要它：TCP 连接、断线重连、心跳看门狗、病害轮询这些东西的生命周期
 * 必须绑在 Activity 上——如果放进 Fragment，切 Tab 触发 Fragment 重建就会把连接弄断。
 * 所以这些逻辑全部留在 {@link MainActivity}，Fragment 只通过这个接口拿数据、发命令。
 *
 * <p>由 {@code MainActivity} 实现，Fragment 里用 {@code (DeviceHost) requireActivity()} 取。
 */
public interface DeviceHost {

    /** 当前是否已连上现场设备 */
    boolean isConnected();

    /** 上次使用的设备 IP（没连过就是默认值） */
    String getDeviceIp();

    /** 上次使用的设备端口 */
    int getDevicePort();

    /** 最近一帧传感器数据；还没收到过就是 null */
    SensorData getLastSensorData();

    /** 连接设备（内部会记住 IP/端口，并启动自动重连与看门狗） */
    void connectDevice(String ip, int port);

    /** 主动断开，同时关掉自动重连 */
    void disconnectDevice();

    /** 打开阈值设置对话框（复用 MainActivity 里原有的实现，命令通道不变） */
    void openThresholdDialog();

    /**
     * 注册/注销传感器监听。
     * 注册时会**立即回放**当前缓存值与连接状态，解决"切回首页数值变 --"的问题；
     * 传 null 表示注销（Fragment onStop 时调用）。
     */
    void setSensorListener(SensorListener listener);

    /** 首页实现的回调 */
    interface SensorListener {
        /** 收到一帧新的传感器数据 */
        void onSensorData(SensorData data);

        /** 连接状态变化（connected + 文案 + 颜色资源） */
        void onConnectionState(boolean connected, String text, int colorRes);
    }
}
