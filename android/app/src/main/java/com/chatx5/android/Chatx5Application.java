package com.chatx5.android;

import android.app.Application;
import android.content.Context;
import android.net.wifi.WifiManager;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

public class Chatx5Application extends Application {
    private static Chatx5Application instance;
    private WifiManager.MulticastLock multicastLock;

    public static Chatx5Application getInstance() {
        return instance;
    }

    @Override
    public void onCreate() {
        super.onCreate();
        instance = this;
        acquireMulticastLock();
        if (!Python.isStarted()) {
            Python.start(new AndroidPlatform(this));
        }
    }

    private void acquireMulticastLock() {
        try {
            WifiManager wifi = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            if (wifi != null) {
                multicastLock = wifi.createMulticastLock("chatx5");
                multicastLock.setReferenceCounted(true);
                multicastLock.acquire();
            }
        } catch (Exception ignored) {}
    }
}