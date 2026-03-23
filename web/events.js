export async function withAsyncAction(action, onError) {
  try {
    return await action();
  } catch (error) {
    if (onError) {
      onError(error);
      return null;
    }
    throw error;
  }
}

export function createDelegatedHandler(routes) {
  return async function handleDelegatedEvent(event) {
    for (const route of routes) {
      const node = event.target.closest(route.selector);
      if (!node) {
        continue;
      }
      await route.handler(node, event);
      return true;
    }
    return false;
  };
}

export function debounce(fn, wait = 120) {
  let timerId = null;
  return (...args) => {
    clearTimeout(timerId);
    timerId = window.setTimeout(() => {
      fn(...args);
    }, wait);
  };
}
