package com.example.smartfarm;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RectF;
import android.util.AttributeSet;
import android.view.View;

import androidx.core.content.ContextCompat;

/**
 * 舒适度得分圆环（2026-10-06）：自绘 View，零第三方依赖。
 *
 * <p>为什么不用 material 的 CircularProgressIndicator：material 1.4.0 对 determinate
 * 模式的支持不稳（1.5 才完善），而自绘一个"轨道 + 进度弧 + 中央数字"只要 60 行。
 * 颜色随档位变：优=绿 / 良=橙 / 差=红（由调用方传入）。
 */
public class ScoreRingView extends View {

    private final Paint trackPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint arcPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint textPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF rect = new RectF();

    private float score = 0f; // 0~100

    public ScoreRingView(Context context) {
        this(context, null);
    }

    public ScoreRingView(Context context, AttributeSet attrs) {
        super(context, attrs);
        float stroke = dp(12);
        trackPaint.setStyle(Paint.Style.STROKE);
        trackPaint.setStrokeWidth(stroke);
        trackPaint.setStrokeCap(Paint.Cap.ROUND);
        trackPaint.setColor(ContextCompat.getColor(context, R.color.colorDivider));
        arcPaint.setStyle(Paint.Style.STROKE);
        arcPaint.setStrokeWidth(stroke);
        arcPaint.setStrokeCap(Paint.Cap.ROUND);
        arcPaint.setColor(0xFF3DBE7B);
        textPaint.setTextAlign(Paint.Align.CENTER);
        textPaint.setTextSize(dp(26));
        textPaint.setFakeBoldText(true);
        textPaint.setColor(ContextCompat.getColor(context, R.color.colorText));
    }

    /** 设置得分与弧色（颜色由档位决定，调用方传） */
    public void setScore(float value, int color) {
        if (value < 0) value = 0;
        if (value > 100) value = 100;
        score = value;
        arcPaint.setColor(color);
        invalidate();
    }

    @Override
    protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        float stroke = trackPaint.getStrokeWidth();
        float inset = stroke / 2f + dp(2);
        rect.set(inset, inset, getWidth() - inset, getHeight() - inset);

        canvas.drawArc(rect, 0, 360, false, trackPaint);
        if (score > 0) {
            // 留一点圆头余量，避免 0~小值时圆头超出
            canvas.drawArc(rect, -90, 360f * score / 100f, false, arcPaint);
        }

        float centerY = getHeight() / 2f - (textPaint.ascent() + textPaint.descent()) / 2f;
        canvas.drawText(String.valueOf(Math.round(score)), getWidth() / 2f, centerY, textPaint);
    }

    private float dp(float v) {
        return v * getResources().getDisplayMetrics().density;
    }
}
