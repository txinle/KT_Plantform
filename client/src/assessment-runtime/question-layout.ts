import { ref, watch, type WatchSource } from 'vue'

// Only scroll the local content/navigation panes, never the page or a focused input.
export function useQuestionLayout(currentQuestion: WatchSource<unknown>) {
  const questionBody = ref<HTMLElement | null>(null)
  const questionTabs = ref<HTMLElement | null>(null)
  const questionNumbers = ref<HTMLElement | null>(null)

  function revealCurrent(container: HTMLElement | null, horizontal: boolean) {
    const current = container?.querySelector<HTMLElement>('[aria-current="step"]')
    if (!container || !current) return
    const bounds = container.getBoundingClientRect()
    const item = current.getBoundingClientRect()
    if (horizontal) {
      if (item.left < bounds.left) container.scrollLeft += item.left - bounds.left
      else if (item.right > bounds.right) container.scrollLeft += item.right - bounds.right
    } else {
      if (item.top < bounds.top) container.scrollTop += item.top - bounds.top
      else if (item.bottom > bounds.bottom) container.scrollTop += item.bottom - bounds.bottom
    }
  }

  function syncQuestionLayout() {
    if (questionBody.value) questionBody.value.scrollTop = 0
    revealCurrent(questionTabs.value, true)
    revealCurrent(questionNumbers.value, false)
  }
  watch(currentQuestion, syncQuestionLayout, { flush: 'post' })
  return { questionBody, questionTabs, questionNumbers, syncQuestionLayout }
}
