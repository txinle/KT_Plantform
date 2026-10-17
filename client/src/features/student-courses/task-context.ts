import type { InjectionKey, Ref, ShallowRef } from 'vue'
import type { StudentTaskCollection } from './api'

export interface StudentTaskContext {
  response: ShallowRef<StudentTaskCollection | undefined>
  loading: Ref<boolean>
  reload: () => Promise<void>
}

export const studentTaskContext: InjectionKey<StudentTaskContext> = Symbol('student tasks')
