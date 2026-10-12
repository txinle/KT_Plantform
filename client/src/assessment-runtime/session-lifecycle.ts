import type { ProcessEventCollector } from './process-sender'
import type { RecordOptions, OnlineOptions, Timer } from './capture-contract'
export interface LifecycleOptions { document?: Document; window?: Window; monotonicNow?: () => number; setInterval?: typeof globalThis.setInterval; clearInterval?: typeof globalThis.clearInterval; heartbeatIntervalSeconds?: number; attemptOpen?: boolean; pendingNavigation?: { token: string; toAssignmentId: number; leaveEventId: string } | null; deferRendered?: boolean; onlineOptions?: OnlineOptions }
export function bindLifecycle(collector: ProcessEventCollector, options: LifecycleOptions = {}) {
  const documentTarget = options.document || globalThis.document;
  const windowTarget = options.window || globalThis.window;
  const monotonicNow = options.monotonicNow || (() => globalThis.performance.now());
  const setIntervalFunction = options.setInterval || globalThis.setInterval?.bind(globalThis);
  const clearIntervalFunction = options.clearInterval || globalThis.clearInterval?.bind(globalThis);
  const heartbeatSeconds = Number(options.heartbeatIntervalSeconds) || 30;
  let visible = documentTarget?.visibilityState !== 'hidden';
  let focused = documentTarget?.hasFocus ? documentTarget.hasFocus() : true;
  let attemptOpen = options.attemptOpen !== false && !options.pendingNavigation;
  let lastInteractionAt = monotonicNow();
  let heartbeatTimer: Timer | null = null;
  const listeners: Array<() => void> = [];

  const record = (eventType: string, payload: Record<string, unknown>, recordOptions?: RecordOptions) => Promise.resolve(
    collector.record(eventType, payload, recordOptions),
  ).catch(() => null);
  const listen = (target: EventTarget | undefined, eventType: string, handler: (event: Event) => unknown, listenerOptions?: AddEventListenerOptions) => {
    if (!target?.addEventListener) return;
    target.addEventListener(eventType, handler, listenerOptions);
    listeners.push(() => target.removeEventListener(eventType, handler, listenerOptions));
  };
  const noteInteraction = () => {
    lastInteractionAt = monotonicNow();
  };

  listen(documentTarget, 'visibilitychange', () => {
    visible = documentTarget.visibilityState !== 'hidden';
    const recorded = record(visible ? 'document_visible' : 'document_hidden', {});
    if (visible) recorded.then(() => collector.flush?.());
    return recorded;
  });
  listen(windowTarget, 'focus', () => {
    focused = true;
    return record('window_focused', {});
  });
  listen(windowTarget, 'blur', () => {
    focused = false;
    return record('window_blurred', {});
  });
  listen(windowTarget, 'pageshow', (event) => record('pageshow', {
    persisted: Boolean((event as PageTransitionEvent).persisted),
  }));
  listen(windowTarget, 'pagehide', async (event) => {
    if (attemptOpen) await closeAttempt('pagehide', null, false, false);
    await record('pagehide', { persisted: Boolean((event as PageTransitionEvent).persisted) });
    await collector.checkpoint?.('pagehide');
    return collector.flush?.({ ignoreVisibility: true, keepalive: true });
  });
  listen(windowTarget, 'online', async () => {
    await record('network_online', {});
    return collector.handleOnline?.(options.onlineOptions || {});
  });
  listen(windowTarget, 'offline', () => record('network_offline', {}));
  ['pointerdown', 'touchstart', 'input'].forEach((eventType) => {
    listen(documentTarget, eventType, noteInteraction, { passive: true });
  });

  const startHeartbeat = () => {
    if (!setIntervalFunction || !attemptOpen || heartbeatTimer !== null) return;
    heartbeatTimer = setIntervalFunction(() => {
      if (!attemptOpen || !visible || !focused) return Promise.resolve();
      return record('activity_heartbeat', {
        attempt_id: collector.attemptId,
        document_visible: true,
        window_focused: true,
        last_device_interaction_age_ms: Math.max(
          0,
          Math.round(monotonicNow() - lastInteractionAt),
        ),
      }).then(() => collector.checkpoint?.('heartbeat'));
    }, Math.max(15, heartbeatSeconds) * 1000);
  };
  startHeartbeat();

  const stopHeartbeat = () => {
    if (heartbeatTimer !== null && clearIntervalFunction) {
      clearIntervalFunction(heartbeatTimer);
    }
    heartbeatTimer = null;
  };
  const closeAttempt = async (
    reason: string, toAssignmentId: number | null = null, flush = true, createCheckpoint = true,
  ) => {
    if (!attemptOpen) return;
    attemptOpen = false;
    stopHeartbeat();
    await record('item_left', {
      leave_reason: reason,
      to_assignment_id: toAssignmentId,
    });
    if (createCheckpoint) {
      await collector.checkpoint?.(reason === 'navigation' ? 'navigation' : 'session_end');
    }
    if (flush) await collector.flushBounded?.(1500, { ignoreVisibility: true, keepalive: true });
  };
  let navigationPreparation: { token: string; toAssignmentId: number; promise: Promise<{ event_id?: string }> } | null = options.pendingNavigation ? {
    token: options.pendingNavigation.token, toAssignmentId: options.pendingNavigation.toAssignmentId,
    promise: Promise.resolve({ event_id: options.pendingNavigation.leaveEventId }),
  } : null;
  const prepareNavigation = (token: string, toAssignmentId: number) => {
    if (navigationPreparation) {
      if (navigationPreparation.token !== token || navigationPreparation.toAssignmentId !== toAssignmentId) {
        throw new Error('Navigation preparation is immutable until its result is known.');
      }
      return navigationPreparation.promise;
    }
    attemptOpen = false;
    stopHeartbeat();
    const promise = collector.record('item_left', { leave_reason: 'navigation', to_assignment_id: toAssignmentId },
      { eventId: globalThis.crypto.randomUUID(), correlationId: token });
    navigationPreparation = { token, toAssignmentId, promise };
    return promise;
  };
  const cancelNavigation = (token: string) => {
    if (!navigationPreparation || navigationPreparation.token !== token) return;
    navigationPreparation = null;
    attemptOpen = options.attemptOpen !== false;
    if (attemptOpen) startHeartbeat();
  };
  const dispose = () => {
    stopHeartbeat();
    listeners.splice(0).forEach((remove) => remove());
  };

  return {
    // item_rendered is the compatibility event name; the payload makes its
    // measurement boundary explicit: stem and controls are interactive.
    ready: (async () => {
      if (!options.deferRendered) await record('item_rendered', { presentation_state: 'interactive' });
      await record('pageshow', { persisted: false });
      await record(visible ? 'document_visible' : 'document_hidden', {});
      await record(focused ? 'window_focused' : 'window_blurred', {});
      await collector.checkpoint?.('resume');
    })(),
    closeAttempt,
    prepareNavigation,
    cancelNavigation,
    dispose,
    isAttemptOpen: () => attemptOpen,
    activate: (open: boolean, pending?: LifecycleOptions['pendingNavigation']) => {
      stopHeartbeat();
      attemptOpen = open && !pending;
      navigationPreparation = pending ? { token: pending.token, toAssignmentId: pending.toAssignmentId, promise: Promise.resolve({ event_id: pending.leaveEventId }) } : null;
      startHeartbeat();
    },
  };
}
