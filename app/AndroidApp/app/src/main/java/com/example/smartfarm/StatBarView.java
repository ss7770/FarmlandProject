package com.example.smartfarm;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.Shader;
import android.util.AttributeSet;
import android.view.View;

import androidx.annotation.Nullable;
import androidx.core.content.ContextCompat;

/**
 * 传感器卡下方的彩色渐变进度条（2026-10-07）。
 *
 * <p>为什么自绘而不用 {@code ProgressBar}：本工程钉 material 1.4.0，
 * determinate 进度条样式在不同主题下表现不稳，且 LayerDrawable+clip 的裁剪会
 * 把右端圆帽切成直角；自绘（同 {@link ScoreRingView} 的思路）能精确控制圆角端帽
 * 与渐变着色，且零第三方依赖、无版本坑。
 *
 * <p>用法：
 * <pre>
 *   StatBarView bar = view.findViewById(R.id.bar_temp);
 *   bar.setProgress(0.5f, R.color.colorBadgeTempStart, R.color.colorBadgeTempEnd);
 * </pre>
 * fraction 语义 = 数值在「适宜区间」内的相对位置（0 放空 / 1 填满），
 * 越界的裁剪由调用方（{@link HomeFragment}）负责。
 */
public class StatBarView extends View {

    /** 轨道色：跟随主题（values-night 已有暗色值），不在此处硬编码 */
    private final Paint trackPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint fillPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF rect = new RectF();

    private float fraction = 0f;
    private boolean hasColors = false;
    /** 记住起止色，宽度变化时按新宽度重建渐变 */
    private int startColor = 0;
    private int endColor = 0;

    public StatBarView(Context context) {
        this(context, null);
    }

    public StatBarView(Context context, @Nullable AttributeSet attrs) {
        this(context, attrs, 0);
    }

    public StatBarView(Context context, @Nullable AttributeSet attrs, int defStyleAttr) {
        super(context, attrs, defStyleAttr);
        trackPaint.setStyle(Paint.Style.FILL);
        trackPaint.setColor(ContextCompat.getColor(context, R.color.colorDivider));
        fillPaint.setStyle(Paint.Style.FILL);
    }

    /**
     * 设置进度与填充渐变色。
     *
     * @param fraction      0~1，越界自动裁剪
     * @param startColorRes 渐变起始色资源（如 R.color.colorBadgeTempStart）
     * @param endColorRes   渐变结束色资源
     */
    public void setProgress(float fraction, int startColorRes, int endColorRes) {
        float f = fraction < 0f ? 0f : (fraction > 1f ? 1f : fraction);
        int start = ContextCompat.getColor(getContext(), startColorRes);
        int end = ContextCompat.getColor(getContext(), endColorRes);
        // 传感器 1 秒一帧，值没变就别重绘（新建 shader 也是开销）
        if (hasColors && f == this.fraction && start == this.startColor && end == this.endColor) {
            return;
        }
        this.fraction = f;
        this.startColor = start;
        this.endColor = end;
        this.hasColors = true;
        rebuildShader();
        invalidate();
    }

    /** 只改进度（保持上次颜色） */
    public void setProgress(float fraction) {
        float f = fraction < 0f ? 0f : (fraction > 1f ? 1f : fraction);
        if (f == this.fraction) return;
        this.fraction = f;
        invalidate();
    }

    /**
     * 渐变锚在「被填充的那一段」上：短条也能同时看到起止两色。
     * 宽度未知时先用屏幕宽兜底，{@link #onSizeChanged} 会按真实宽度重建。
     */
    private void rebuildShader() {
        if (!hasColors) return;
        int w = getWidth() > 0 ? getWidth() : getResources().getDisplayMetrics().widthPixels;
        int fillW = Math.max(1, (int) (w * fraction));
        fillPaint.setShader(new LinearGradient(0f, 0f, fillW, 0f,
                startColor, endColor, Shader.TileMode.CLAMP));
    }

    @Override
    protected void onSizeChanged(int w, int h, int oldw, int oldh) {
        super.onSizeChanged(w, h, oldw, oldh);
        rebuildShader();
    }

    @Override
    protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);

        final int w = getWidth();
        final int h = getHeight();
        if (w <= 0 || h <= 0) return;

        final float r = h / 2f;   // 圆角 = 高度一半（胶囊端帽）

        // ① 轨道
        rect.set(0f, 0f, w, h);
        canvas.drawRoundRect(rect, r, r, trackPaint);

        // ② 填充（渐变 + 圆角端帽）
        if (fraction <= 0f || !hasColors) return;
        float fillW = w * fraction;
        if (fillW < h) fillW = h;   // 极小值时留一个圆点，避免视觉上"没有"
        rect.set(0f, 0f, Math.min(fillW, w), h);
        canvas.drawRoundRect(rect, r, r, fillPaint);
    }
}
