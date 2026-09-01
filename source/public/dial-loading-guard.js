(() => {
  const selector = '.dial-wrap.mode-daily .dial-slot-value, .dial-wrap.mode-weekly .dial-slot-value'

  const sync = () => {
    document.querySelectorAll(selector).forEach((node) => {
      const waiting = node.textContent.trim() === '—'
      node.style.visibility = waiting ? 'hidden' : 'visible'
      node.style.opacity = waiting ? '0' : '1'
    })
  }

  const observer = new MutationObserver(sync)
  observer.observe(document.documentElement, { subtree: true, childList: true, characterData: true })

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', sync, { once: true })
  } else {
    sync()
  }
})()
