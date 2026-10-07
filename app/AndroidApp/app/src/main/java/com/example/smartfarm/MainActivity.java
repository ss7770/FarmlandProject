package com.example.smartfarm;

import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;
import androidx.fragment.app.Fragment;
import androidx.fragment.app.FragmentTransaction;

import android.content.DialogInterface;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.text.TextUtils;
import android.util.Log;
import android.view.MenuItem;
import android.view.View;
import android.widget.EditText;
import android.widget.Toast;

import com.google.android.material.bottomnavigation.BottomNavigationView;

/**
 * 主界面（2026-10-05 起改造为「底部 Tab 宿主」）。
 *
 * <p>结构：FragmentContainerView + BottomNavigationView，四个 Tab —— 首页 / 数据 / 病害 / 设置。
 * 其中「数据」「病害」是 WebView 直接加载 Flask 页面（见 {@link WebTabFragment}），
 * 「首页」「设置」是原生 Fragment。
 *
 * <p>⚠️ 关键设计：TCP 连接、断线自动重连、心跳看门狗、病害轮询、阈值命令**全部留在这里**，
 * 因为它们的生命周期必须跨越 Tab 切换。Fragment 只通过 {@link DeviceHost} 拿数据。
 * 切 Tab 用 hide/show（不是 replace），否则 WebView 会被销毁导致每次切页都白屏重载。
 */
public class MainActivity extends AppCompatActivity implements DeviceHost {

    private static final String TAG = "MainActivity";

    /** 断线自动重连间隔（毫秒） */
    private static final long RECONNECT_DELAY_MS = 5000L;
    /** 心跳超时：STM32 每 1 秒发一次数据，10 秒没收到视为连接已死 */
    private static final long DATA_TIMEOUT_MS = 10000L;
    /** 看板轮询周期 */
    private static final long WATCHDOG_PERIOD_MS = 5000L;

    /** FragmentManager 里四个 Tab 的 tag */
    private static final String TAG_HOME = "tab_home";
    private static final String TAG_DATA = "tab_data";
    private static final String TAG_DISEASE = "tab_disease";
    private static final String TAG_SETTINGS = "tab_settings";

    private BottomNavigationView bottomNav;

    private TcpClient tcpClient;
    private boolean isConnected = false;
    /** 用户主动点过"连接"且未主动断开时为 true——自动重连只在这种状态下生效 */
    private boolean userRequestedConnect = false;
    private String lastIp;
    private int lastPort;
    /** 最近一次收到传感器数据的时刻（elapsedRealtime），用于心跳判活 */
    private long lastDataTime;

    /** 最近一帧数据：切回首页时回放，避免数值变回 "--" */
    private SensorData lastSensorData;
    /** 首页注册的监听者；首页不可见时为 null */
    private DeviceHost.SensorListener sensorListener;
    /** 当前连接状态文案与颜色（供回放） */
    private String connText;
    private int connColorRes = R.color.colorError;

    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    /** 自动重连任务 */
    private final Runnable reconnectRunnable = new Runnable() {
        @Override
        public void run() {
            if (!userRequestedConnect || isConnected) return;
            Log.i(TAG, "auto-reconnecting to " + lastIp + ":" + lastPort);
            notifyState(false, getString(R.string.connecting), R.color.colorWarning);
            connectInternal();
        }
    };
    /** 连接看门狗：连接着却长时间收不到数据 → 强制断开触发重连 */
    private final Runnable watchdogRunnable = new Runnable() {
        @Override
        public void run() {
            if (isConnected
                    && SystemClock.elapsedRealtime() - lastDataTime > DATA_TIMEOUT_MS) {
                Log.w(TAG, "no data for " + DATA_TIMEOUT_MS + "ms, forcing reconnect");
                if (tcpClient != null) {
                    tcpClient.disconnect();   // 会回调 onDisconnected → 走自动重连
                }
            }
            if (userRequestedConnect) {
                mainHandler.postDelayed(this, WATCHDOG_PERIOD_MS);
            }
        }
    };

    /** 病害巡检轮询器（30 秒轮询后端，新病害弹通知 + TTS 播报） */
    private DiseasePoller diseasePoller;

    private int tempThreshold = 30;
    private int humiThreshold = 50;
    private int lightThreshold = 800;
    private int waterThreshold = 1000;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        // 标题固定「慧耕沃野」：Fragment 里一律不要再改标题
        if (getSupportActionBar() != null) {
            getSupportActionBar().setTitle(R.string.app_title);
        }

        lastIp = DevicePrefs.getIp(this);
        lastPort = DevicePrefs.getPort(this);

        bottomNav = findViewById(R.id.bottom_nav);
        bottomNav.setOnItemSelectedListener(new BottomNavigationView.OnItemSelectedListener() {
            @Override
            public boolean onNavigationItemSelected(MenuItem item) {
                showTab(item.getItemId());
                return true;
            }
        });

        if (savedInstanceState == null) {
            // 四个 Fragment 一次建好：网页 Tab 此时只建壳，不加载 URL（首次可见才加载）
            Fragment home = new HomeFragment();
            Fragment data = WebTabFragment.newInstance(WebTabFragment.PATH_DATA);
            Fragment disease = WebTabFragment.newInstance(WebTabFragment.PATH_DISEASE);
            Fragment settings = new SettingsFragment();
            getSupportFragmentManager().beginTransaction()
                    .add(R.id.fragment_container, home, TAG_HOME)
                    .add(R.id.fragment_container, data, TAG_DATA).hide(data)
                    .add(R.id.fragment_container, disease, TAG_DISEASE).hide(disease)
                    .add(R.id.fragment_container, settings, TAG_SETTINGS).hide(settings)
                    .commitNow();

            // 首次进入：触发一次选中 + 显式显示首页（showTab 幂等，双保险）
            bottomNav.setSelectedItemId(R.id.nav_home);
            showTab(R.id.nav_home);
        } else {
            // 重建（切换深色模式等）：BottomNavigationView 会自己恢复上次选中的 Tab，
            // 这里按它当前的值把 show/hide 落回正确状态——否则切完主题会被弹回首页。
            showTab(bottomNav.getSelectedItemId());
        }

        // 重建后恢复连接意图：切主题/旋屏导致 Activity 重建会断开 TCP，
        // 上次"连着"的话这里自动重连一次，不用用户手动点（2026-10-06）。
        if (savedInstanceState != null && AppPrefs.wasConnected(this)) {
            userRequestedConnect = true;
            mainHandler.postDelayed(watchdogRunnable, WATCHDOG_PERIOD_MS);
            notifyState(false, getString(R.string.connecting), R.color.colorWarning);
            connectInternal();
        }

        diseasePoller = new DiseasePoller(this, new DiseasePoller.Listener() {
            @Override
            public void onNewRecords(long newLastId, String diseaseLabel, double confidence) {
                Toast.makeText(MainActivity.this,
                        "巡检提醒：检测到病害，已通知", Toast.LENGTH_LONG).show();
            }
        });
        diseasePoller.start();
    }

    /** 切 Tab：hide 掉其他三个，show 目标（不 replace，保住 WebView 实例） */
    private void showTab(int itemId) {
        String target;
        if (itemId == R.id.nav_data) {
            target = TAG_DATA;
        } else if (itemId == R.id.nav_disease) {
            target = TAG_DISEASE;
        } else if (itemId == R.id.nav_settings) {
            target = TAG_SETTINGS;
        } else {
            target = TAG_HOME;
        }

        FragmentTransaction ft = getSupportFragmentManager().beginTransaction();
        for (String tag : new String[]{TAG_HOME, TAG_DATA, TAG_DISEASE, TAG_SETTINGS}) {
            Fragment f = getSupportFragmentManager().findFragmentByTag(tag);
            if (f == null) continue;
            if (tag.equals(target)) {
                ft.show(f);
            } else {
                ft.hide(f);
            }
        }
        ft.commit();
    }

    // ==================== DeviceHost 实现 ====================

    @Override
    public boolean isConnected() {
        return isConnected;
    }

    @Override
    public String getDeviceIp() {
        return TextUtils.isEmpty(lastIp) ? DevicePrefs.DEFAULT_IP : lastIp;
    }

    @Override
    public int getDevicePort() {
        return lastPort > 0 ? lastPort : DevicePrefs.DEFAULT_PORT;
    }

    @Override
    public SensorData getLastSensorData() {
        return lastSensorData;
    }

    @Override
    public void connectDevice(String ip, int port) {
        if (TextUtils.isEmpty(ip)) {
            Toast.makeText(this, R.string.enter_ip, Toast.LENGTH_SHORT).show();
            return;
        }
        lastIp = ip.trim();
        lastPort = port;
        DevicePrefs.set(this, lastIp, lastPort);

        userRequestedConnect = true;
        mainHandler.removeCallbacks(reconnectRunnable);
        mainHandler.removeCallbacks(watchdogRunnable);
        mainHandler.postDelayed(watchdogRunnable, WATCHDOG_PERIOD_MS);

        notifyState(false, getString(R.string.connecting), R.color.colorWarning);
        connectInternal();
    }

    @Override
    public void disconnectDevice() {
        // 用户主动断开：连"上次连着"的记忆一起清掉，免得之后重建又自己连回来
        AppPrefs.setWasConnected(this, false);
        disconnect();
    }

    @Override
    public void openThresholdDialog() {
        showThresholdDialog();
    }

    @Override
    public void setSensorListener(DeviceHost.SensorListener listener) {
        sensorListener = listener;
        if (listener == null) return;
        // 立即回放：新注册（或切回首页）时把当前值和连接状态补上
        if (lastSensorData != null) {
            listener.onSensorData(lastSensorData);
        }
        listener.onConnectionState(isConnected,
                connText == null ? getString(R.string.disconnected) : connText,
                connColorRes);
    }

    /** 统一的状态出口：记录一份 + 推给首页（首页不在时 listener 为 null，天然跳过） */
    private void notifyState(boolean connected, String text, int colorRes) {
        connText = text;
        connColorRes = colorRes;
        if (sensorListener != null) {
            sensorListener.onConnectionState(connected, text, colorRes);
        }
    }

    /** 用记住的 ip/port 建立连接（手动连接和自动重连共用） */
    private void connectInternal() {
        tcpClient = new TcpClient(lastIp, lastPort);
        tcpClient.setConnectionListener(new TcpClient.OnConnectionListener() {
            @Override
            public void onConnected() {
                isConnected = true;
                lastDataTime = SystemClock.elapsedRealtime();
                AppPrefs.setWasConnected(MainActivity.this, true);   // 记住连接意图，供重建后自动重连
                notifyState(true, getString(R.string.connected), R.color.colorSuccess);
                Toast.makeText(MainActivity.this, "连接成功", Toast.LENGTH_SHORT).show();
            }

            @Override
            public void onDisconnected() {
                isConnected = false;
                if (userRequestedConnect) {
                    // 断线自动重连：显示倒计时状态，5 秒后重试
                    notifyState(false, "已断开，正在自动重连…", R.color.colorWarning);
                    mainHandler.removeCallbacks(reconnectRunnable);
                    mainHandler.postDelayed(reconnectRunnable, RECONNECT_DELAY_MS);
                } else {
                    notifyState(false, getString(R.string.disconnected), R.color.colorError);
                }
            }

            @Override
            public void onConnectionFailed(String error) {
                isConnected = false;
                if (userRequestedConnect) {
                    notifyState(false, "连接失败，正在自动重连…", R.color.colorWarning);
                    mainHandler.removeCallbacks(reconnectRunnable);
                    mainHandler.postDelayed(reconnectRunnable, RECONNECT_DELAY_MS);
                } else {
                    notifyState(false, getString(R.string.disconnected), R.color.colorError);
                    Toast.makeText(MainActivity.this, error, Toast.LENGTH_SHORT).show();
                }
            }
        });

        tcpClient.setDataReceivedListener(new TcpClient.OnDataReceivedListener() {
            @Override
            public void onDataReceived(SensorData data) {
                lastDataTime = SystemClock.elapsedRealtime();
                lastSensorData = data;                                   // ① 缓存（供回放）
                if (sensorListener != null) {
                    sensorListener.onSensorData(data);                   // ② 推送首页
                }
                // ③ 原逻辑不动：节流上报到 Flask /api/sensor，持续写入 sensor.db
                SensorUploader.report(MainActivity.this, data);
            }
        });

        lastDataTime = SystemClock.elapsedRealtime();
        tcpClient.connect();
    }

    private void disconnect() {
        // 用户主动断开：关掉自动重连和看门狗
        // ⚠️ 这里不清 AppPrefs.wasConnected：onDestroy 也走这个方法（切深色模式会重建 Activity），
        //    清了就没法在重建后自动重连。清除只发生在用户真正点"断开"的 disconnectDevice()。
        userRequestedConnect = false;
        mainHandler.removeCallbacks(reconnectRunnable);
        mainHandler.removeCallbacks(watchdogRunnable);
        if (tcpClient != null) {
            tcpClient.disconnect();
            tcpClient = null;
        }
        isConnected = false;
        notifyState(false, getString(R.string.disconnected), R.color.colorError);
    }

    /** 阈值设置对话框：行为与改造前一致（未连接不能发） */
    private void showThresholdDialog() {
        if (!isConnected || tcpClient == null) {
            Toast.makeText(this, R.string.threshold_need_connect, Toast.LENGTH_SHORT).show();
            return;
        }

        View dialogView = getLayoutInflater().inflate(R.layout.dialog_threshold, null);

        final EditText etTemp = dialogView.findViewById(R.id.et_temp_threshold);
        final EditText etHumi = dialogView.findViewById(R.id.et_humi_threshold);
        final EditText etLight = dialogView.findViewById(R.id.et_light_threshold);
        final EditText etWater = dialogView.findViewById(R.id.et_water_threshold);

        etTemp.setText(String.valueOf(tempThreshold));
        etHumi.setText(String.valueOf(humiThreshold));
        etLight.setText(String.valueOf(lightThreshold));
        etWater.setText(String.valueOf(waterThreshold));

        AlertDialog.Builder builder = new AlertDialog.Builder(this);
        builder.setView(dialogView);

        builder.setPositiveButton(R.string.save, new DialogInterface.OnClickListener() {
            @Override
            public void onClick(DialogInterface dialog, int which) {
                try {
                    int temp = Integer.parseInt(etTemp.getText().toString().trim());
                    int humi = Integer.parseInt(etHumi.getText().toString().trim());
                    int light = Integer.parseInt(etLight.getText().toString().trim());
                    int water = Integer.parseInt(etWater.getText().toString().trim());

                    tempThreshold = temp;
                    humiThreshold = humi;
                    lightThreshold = light;
                    waterThreshold = water;

                    tcpClient.sendThresholdCommand(temp, humi, light, 10, water);
                    Toast.makeText(MainActivity.this, "阈值设置已发送", Toast.LENGTH_SHORT).show();
                } catch (NumberFormatException e) {
                    Toast.makeText(MainActivity.this, "请输入有效的数值", Toast.LENGTH_SHORT).show();
                }
            }
        });

        builder.setNegativeButton(R.string.cancel, null);
        builder.show();
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        // 退出页面：清掉所有定时任务再断开
        userRequestedConnect = false;
        mainHandler.removeCallbacks(reconnectRunnable);
        mainHandler.removeCallbacks(watchdogRunnable);
        sensorListener = null;
        disconnect();
        if (diseasePoller != null) {
            diseasePoller.stop();
            diseasePoller = null;
        }
    }
}
