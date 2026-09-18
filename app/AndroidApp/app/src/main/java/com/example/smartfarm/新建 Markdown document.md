好，我们不绕弯子，直接告诉你该怎么做。**你不需要理解原理，只需要照着步骤操作，就能让APP在WiFi恢复后自动重连。**

---

### 你需要改两个文件

1. `MainActivity.java`（主要改动在这里）
2. `TcpClient.java`（加一行代码）

---

### 第一步：在 `MainActivity.java` 里加两段代码27-31

**位置1：在文件开头，和 `private boolean isConnected = false;` 放在一起**

```java
// 在 MainActivity 类的开头加入这两行
private String mLastIp = "";
private int mLastPort = 0;
private android.os.Handler mReconnectHandler = new android.os.Handler(android.os.Looper.getMainLooper());
private int mReconnectAttempts = 0;
```

**位置2：找到 `connect()` 方法，在 `int port = Integer.parseInt(portStr);` 后面加两行**95-96

```java
int port = Integer.parseInt(portStr);

// 保存IP和端口，用于自动重连
mLastIp = ip;
mLastPort = port;
```

**位置3：找到 `onDisconnected` 方法（在 `setConnectionListener` 里面），在 `Toast.makeText` 后面加这段**112-137

```java
@Override
public void onDisconnected() {
    isConnected = false;
    btnConnect.setText(R.string.connect);
    btnConnect.setEnabled(true);
    tvStatus.setText(R.string.disconnected);
    tvStatus.setTextColor(getResources().getColor(R.color.colorError));

    // ===== 新增自动重连代码 =====
    // 如果之前连接过，3秒后自动重连
    if (!TextUtils.isEmpty(mLastIp) && mLastPort != 0) {
        // 取消之前的重连任务
        mReconnectHandler.removeCallbacksAndMessages(null);
        // 延迟3秒后执行重连
        mReconnectHandler.postDelayed(new Runnable() {
            @Override
            public void run() {
                // 如果当前未连接，则发起重连
                if (!isConnected) {
                    connect();
                }
            }
        }, 3000);
    }
    // ==========================
}
```

---

### 第二步：在 `TcpClient.java` 里加一行

在 `TcpClient.java` 的 `disconnect()` 方法里，找到 `notifyDisconnected();` 这一行，确保它在 `catch` 之后也会被执行。

**你只需要确认 `notifyDisconnected();` 在方法最后被调用就行了。** 你的代码里已经有了，所以这一步其实不需要改。

---

### 第三步：重新编译运行

1. 在 Android Studio 里点击 `Build` → `Make Project`。
2. 如果没有报错，点击 `Run`，安装到手机上。
3. 测试：
   - 连接设备（输入IP和端口）
   - 断开WiFi热点
   - 等10秒
   - 重新打开热点
   - **观察APP是否在3秒后自动重新连接**

---

### 如果你复制代码后编译报错

把报错信息发给我，我帮你改。大概率是 `TextUtils` 或 `Handler` 的引用问题，加一行 `import` 就能解决。

---

你现在可以直接复制上面标出来的三段代码，粘贴到对应位置，然后编译测试。不需要理解原理，只需要按步骤操作。如果编译不通过，把报错截图发我，我帮你修复。
