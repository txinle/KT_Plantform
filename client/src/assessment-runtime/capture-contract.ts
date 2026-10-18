export interface CaptureEvent {
  event_type: string
  event_version: number
  session_id: number
  attempt_id: number | null
  client_sequence_no: number
  client_wall_time_utc: string
  client_monotonic_ms: number
  client_time_origin: string
  payload: Record<string, unknown>
  quality_flags: string[]
  app_version: string
  correlation_id?: string
  event_id?: string
  time_sync_id?: string
}
export interface CaptureCheckpoint {
  checkpoint_id: string
  session_id: number
  client_instance_id: string
  highest_emitted_sequence_no: number
  highest_acked_sequence_no: number
  highest_contiguous_acked_sequence_no: number
  pending_event_count: number
  failed_event_count: number
  missing_local_sequence_ranges: number[][]
  client_monotonic_ms: number
  client_wall_time_utc: string
  client_time_origin: string
  time_sync_id: string | null
  trigger: string
  quality_flags: string[]
  app_version: string
}
export interface ProcessSnapshot {
  client_instance_id: string
  event_boundary: number
  events: CaptureEvent[]
  checkpoints: CaptureCheckpoint[]
}
export interface CaptureBundle extends ProcessSnapshot { version: 1 }
export interface CaptureBundleCapability { enabled: boolean; version: 1; max_events: number; max_checkpoints: number; max_bytes: number; mutation_receipts?: boolean; recovery_supported?: boolean }
export interface CaptureMutation extends DurableIntent {
  url: string
  body: Record<string, unknown>
  sessionId: number
  attemptId: number
  operationId?: string
  processSnapshot: CaptureBundle
  phase?: 'prepared' | 'sent'
  materialFingerprint?: string
}
export interface CaptureMutationRejection {
  code: string
  status: number
  fields: Record<string, unknown>
  materialFingerprint: string
  captureOperationId?: string
  rejectedAt: string
  materialSynchronized?: boolean
}
export interface DurableIntent { processSnapshot?: ProcessSnapshot; [key: string]: unknown }
export interface FactRecord {
  client_instance_id: string
  client_sequence_no: number
  outbox_order: number
  event: CaptureEvent
}
export interface CaptureRejection { client_sequence_no: number; code?: string; permanent?: boolean; http_status?: number }
export interface CaptureState {
  highestEmitted: number
  highestAcked: number
  highestContiguousAcked: number
  pending: number
  failed: number
  missingLocalRanges: number[][]
}
export interface CaptureStore {
  isFallback: boolean
  enqueue(id: string, factory: (sequence: number) => CaptureEvent): Promise<CaptureEvent>
  restoreResponse(session: number, attempt: number): Promise<CaptureEvent | null>
  restoreSubmissionIntent(session: number, attempt: number): Promise<DurableIntent | null>
  saveSubmissionIntent(session: number, attempt: number, intent: DurableIntent | null): Promise<void>
  restoreNavigationIntent(session: number, attempt: number): Promise<DurableIntent | null>
  saveNavigationIntent(session: number, attempt: number, intent: DurableIntent | null): Promise<void>
  clearResponseRecovery(session: number, attempt: number): Promise<void>
  freezeBoundary(id: string): Promise<Record<string, number>>
  pendingEvents(id: string, boundary: number): Promise<CaptureEvent[]>
  peekBatch(limit?: number, boundary?: Record<string, number> | null): Promise<{ clientInstanceId: string; events: CaptureEvent[] } | null>
  deleteAcknowledged(id: string, sequences: number[]): Promise<void>
  count(): Promise<number>
  captureState(id: string): Promise<CaptureState>
  enqueueCheckpoint(checkpoint: CaptureCheckpoint): Promise<unknown>
  pendingCheckpoints(includeOwned?: boolean): Promise<CaptureCheckpoint[]>
  deleteCheckpoint(id: string): Promise<void>
  moveToDeadLetter(id: string, sequence: number, rejection: CaptureRejection): Promise<void>
  readMetadata<T>(key: string): Promise<T | null>
  writeMetadata<T>(key: string, value: T | null): Promise<void>
  rejectCaptureMutation(intent: CaptureMutation, rejection: CaptureMutationRejection): Promise<boolean>
  pendingCaptureMutations(sessionId: number): Promise<CaptureMutation[]>
}
export interface RecordOptions {
  wallTime?: string
  monotonicMs?: number
  qualityFlags?: string[]
  materialize?: () => Promise<Record<string, unknown>>
  eventVersion?: number
  correlationId?: string
  eventId?: string
}
export interface FlushOptions { boundary?: Record<string, number>; ignoreVisibility?: boolean; keepalive?: boolean; operationId?: string; timeoutMs?: number }
export interface ProcessConfirmation {
  capture_operation_id?: string
  committed?: boolean
  acknowledgements?: Array<{ client_sequence_no: number; event_id?: string; server_ingest_sequence_no?: number }>
  checkpoint_ids?: string[]
  navigation_token?: string
  to_assignment_id?: number
  writer_lease_released?: boolean
  active_response_event_id?: string | null
}
export interface CaptureResponse extends ProcessConfirmation {
  success?: boolean
  checkpoint_id?: string
  time_sync_id?: string
  sync_quality?: string
  rejections?: CaptureRejection[]
  error?: { code?: string }
}
export interface SyncUrls { createUrl?: string; finalizeUrl?: string }
export interface OnlineOptions { synchronize?: boolean; syncUrls?: SyncUrls }
export type Timer = ReturnType<typeof globalThis.setTimeout>
export interface SenderOptions {
  captureBundle?: CaptureBundleCapability
  sessionId: number
  attemptId: number
  endpoint: string
  checkpointEndpoint?: string
  csrfToken?: string
  appVersion?: string
  correlationId?: string | null
  outbox: CaptureStore
  fetch?: typeof fetch
  wallNow?: () => Date
  monotonicNow?: () => number
  timeOrigin?: string
  isOnline?: () => boolean
  isVisible?: () => boolean
  setTimeout?: typeof globalThis.setTimeout
  clearTimeout?: typeof globalThis.clearTimeout
  setInterval?: typeof globalThis.setInterval
  clearInterval?: typeof globalThis.clearInterval
  autoSchedule?: boolean
  uploadDelayMs?: number
  uuid?: () => string
}
