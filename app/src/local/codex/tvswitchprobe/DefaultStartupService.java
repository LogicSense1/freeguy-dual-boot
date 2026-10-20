// SPDX-License-Identifier: Apache-2.0
package local.codex.tvswitchprobe;

import android.accessibilityservice.AccessibilityService;
import android.view.accessibility.AccessibilityEvent;

/** Lifecycle binding only: no UI events, window content, gestures or polling. */
public final class DefaultStartupService extends AccessibilityService {
    @Override protected void onServiceConnected(){DefaultBootJob.schedule(this);}
    @Override public void onAccessibilityEvent(AccessibilityEvent event){}
    @Override public void onInterrupt(){}
}
