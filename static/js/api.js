let unauthorizedHandler = null;

export function setUnauthorizedHandler(handler) {
  unauthorizedHandler = typeof handler === 'function' ? handler : null;
}

export async function apiFetch(url, options = {}, action = 'request') {
  let res;
  try {
    res = await fetch(url, {
      credentials: 'same-origin',
      ...options,
      headers: {
        Accept: 'application/json',
        ...(options.headers || {}),
      },
    });
  } catch (error) {
    throw new Error(`Network error while ${action}: ${error.message || 'Failed to fetch'}`);
  }

  return readApiResponse(res, action);
}

export function isNetworkFetchError(error) {
  return error instanceof TypeError || /Failed to fetch|Network error/i.test(error?.message || '');
}

async function readApiResponse(res, action = 'request') {
  const contentType = res.headers.get('content-type') || '';
  const bodyText = await res.text();
  let data = null;

  if (bodyText && contentType.includes('application/json')) {
    try {
      data = JSON.parse(bodyText);
    } catch {
      data = null;
    }
  }

  if (!res.ok) {
    if (res.status === 401) {
      unauthorizedHandler?.();
      throw new Error('Session expired. Please enter the access key.');
    }

    const message = data?.detail || data?.error || data?.message || bodyText || `${action} failed`;
    const status = res.status ? ` (${res.status})` : '';
    throw new Error(`${message}${status}`);
  }

  if (!data) {
    throw new Error(bodyText || `Server returned an empty response while ${action}`);
  }

  return data;
}
