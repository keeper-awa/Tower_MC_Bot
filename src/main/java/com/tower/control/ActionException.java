package com.tower.control;

/**
 * 动作执行失败：携带协议错误码（《Tower协议.md》§8）。
 *
 * <p>由动作处理器抛出，Dispatcher 将其转换为失败响应
 * {@code {"id":N,"ok":false,"error":{"code":C,"message":"..."}}}。
 */
public class ActionException extends RuntimeException {
    public final int code;

    public ActionException(int code, String message) {
        super(message);
        this.code = code;
    }
}
