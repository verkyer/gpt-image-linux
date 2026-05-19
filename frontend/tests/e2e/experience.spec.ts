import { expect, type Page, test } from '@playwright/test';

const PNG_BYTES = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4//8/AwAI/AL+X1N6AAAAAElFTkSuQmCC',
  'base64'
);

type MockOptions = {
  authenticated?: boolean;
  editUploadFailure?: boolean;
  runningJobs?: unknown[];
  historyJobs?: unknown[];
};

const baseGalleryImages = [
  {
    id: 'img-1',
    prompt: 'First gallery image',
    size: '1024x1024',
    filename: 'img-1.png',
    thumbnail_url: '/api/thumb/img-1.png',
    created_at: '2026-05-18T12:00:00Z',
    completed_at: '2026-05-18T20:00:01+08:00',
    image_width: 1,
    image_height: 1,
    model: 'gpt-image-2',
    api_preset_name: 'Default',
    duration: '1.00s',
    favorite: false,
    bytes: 68
  },
  {
    id: 'img-2',
    prompt: 'Second gallery image',
    size: '1536x1024',
    filename: 'img-2.png',
    thumbnail_url: '/api/thumb/img-2.png',
    created_at: '2026-05-18T12:01:00Z',
    completed_at: '2026-05-18T20:01:01+08:00',
    image_width: 1,
    image_height: 1,
    model: 'gpt-image-2',
    api_preset_name: 'Default',
    duration: '1.10s',
    favorite: true,
    bytes: 68
  }
];

const settingsResponse = {
  active_preset_id: 'default',
  api_url: 'https://api.example.com',
  api_key_masked: '********',
  has_api_key: true,
  api_key_source: 'stored',
  api_path: '/v1/images/generations',
  default_model: 'preset-default-model',
  has_upstream_socks5_proxy: false,
  upstream_socks5_proxy_masked: '',
  presets: [
    {
      id: 'default',
      name: 'Default',
      api_url: 'https://api.example.com',
      api_key_masked: '********',
      has_api_key: true,
      api_key_source: 'stored',
      api_path: '/v1/images/generations',
      default_model: 'preset-default-model'
    }
  ]
};

function json(body: unknown, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(body)
  };
}

function galleryResponse(images = baseGalleryImages, includeTotalBytes = false) {
  return {
    total: images.length,
    total_bytes: includeTotalBytes ? images.reduce((sum, image) => sum + image.bytes, 0) : 0,
    page: 1,
    page_size: 9,
    total_pages: 1,
    has_prev: false,
    has_next: false,
    images,
    filter_options: {
      models: ['gpt-image-2'],
      presets: ['Default'],
      sizes: ['1024x1024', '1536x1024']
    }
  };
}

function job(jobId: string, prompt: string, status: 'running' | 'success' = 'success') {
  return {
    job_id: jobId,
    status,
    stage: status === 'success' ? 'completed' : 'waiting_for_api',
    message: status === 'success' ? 'Image generation completed' : 'Waiting for upstream API response',
    operation: 'generation',
    image_id: 'img-1',
    image_url: '/api/image/img-1.png',
    prompt,
    size: '1024x1024',
    created_at: '2026-05-18T12:00:00Z',
    updated_at: '2026-05-18T12:00:01Z',
    completed_at: status === 'success' ? '2026-05-18T20:00:01+08:00' : null,
    image_width: 1,
    image_height: 1,
    model: 'gpt-image-2',
    duration: status === 'success' ? '1.00s' : null,
    stage_timings: {
      upstream_wait: 1.2,
      download_decode: 0.4,
      validate: 0.1,
      thumbnail: 0.2,
      db_insert: 0.3
    }
  };
}

function manyJobs(count: number) {
  return Array.from({ length: count }, (_, index) => job(`job-${index}`, `history prompt ${index}`, 'running'));
}

async function mockApi(page: Page, options: MockOptions = {}) {
  let authenticated = options.authenticated ?? true;
  const runningJobs = options.runningJobs ?? [];
  const historyJobs = options.historyJobs ?? [job('history-1', 'saved prompt')];

  await page.addInitScript(() => {
    localStorage.setItem('gpt-image-panel-language', 'en');
  });

  await page.route('**/*', async (route) => {
    const request = route.request();
    const url = new URL(request.url());

    if (url.pathname === '/api/access/status') {
      await route.fulfill(json({ authenticated, expires_at: authenticated ? '2026-05-18T14:00:00Z' : null }));
      return;
    }
    if (url.pathname === '/api/access') {
      const body = JSON.parse(request.postData() || '{}');
      authenticated = body.access_key === 'open-sesame';
      await route.fulfill(json({ authenticated, expires_at: authenticated ? '2026-05-18T14:00:00Z' : null }));
      return;
    }
    if (url.pathname === '/api/version') {
      await route.fulfill(json({ version: 'v0.test', github_repo: 'test/repo', release_url: null }));
      return;
    }
    if (url.pathname === '/api/version/latest') {
      await route.fulfill(json({ latest_version: null, has_update: false, checked_at: null }));
      return;
    }
    if (url.pathname === '/api/settings') {
      await route.fulfill(json(settingsResponse));
      return;
    }
    if (url.pathname.endsWith('/health') && url.pathname.startsWith('/api/settings/presets/')) {
      await route.fulfill(json({ status: 'ok', checks: [{ name: 'api_url', status: 'ok', message: 'ok' }] }));
      return;
    }
    if (url.pathname === '/api/gallery' && request.method() === 'GET') {
      const prompt = url.searchParams.get('prompt') || '';
      const images = prompt
        ? baseGalleryImages.filter((image) => image.prompt.toLowerCase().includes(prompt.toLowerCase()))
        : baseGalleryImages;
      await route.fulfill(json(galleryResponse(images, url.searchParams.get('include_total_bytes') === 'true')));
      return;
    }
    if (url.pathname.match(/^\/api\/gallery\/[^/]+$/) && request.method() === 'GET') {
      const id = decodeURIComponent(url.pathname.split('/').pop() || '');
      const image = baseGalleryImages.find((entry) => entry.id === id);
      await route.fulfill(image ? json(image) : json({ detail: 'Gallery entry not found' }, 404));
      return;
    }
    if (url.pathname.match(/^\/api\/gallery\/[^/]+$/) && request.method() === 'DELETE') {
      await route.fulfill(json({ status: 'ok', message: 'Deleted gallery entry and 1 image file(s)' }));
      return;
    }
    if (url.pathname === '/api/gallery' && request.method() === 'DELETE') {
      await route.fulfill(json({ status: 'ok', message: 'Deleted all images' }));
      return;
    }
    if (url.pathname === '/api/gallery/batch/delete') {
      const body = JSON.parse(request.postData() || '{}');
      await route.fulfill(json({ status: 'ok', count: body.ids?.length || 0, file_count: body.ids?.length || 0 }));
      return;
    }
    if (url.pathname === '/api/gallery/batch/favorite') {
      const body = JSON.parse(request.postData() || '{}');
      await route.fulfill(json({ status: 'ok', count: body.ids?.length || 0, file_count: 0 }));
      return;
    }
    if (url.pathname.match(/^\/api\/gallery\/[^/]+\/favorite$/)) {
      await route.fulfill(json({ ...baseGalleryImages[0], favorite: true }));
      return;
    }
    if (url.pathname.startsWith('/api/thumb/') || url.pathname.startsWith('/api/image/')) {
      await route.fulfill({ status: 200, contentType: 'image/png', body: PNG_BYTES });
      return;
    }
    if (url.pathname === '/api/generate' && request.method() === 'POST') {
      await route.fulfill(json({ job_id: 'job-generated', status: 'queued', stage: 'queued', operation: 'generation' }, 202));
      return;
    }
    if (url.pathname === '/api/edits/from-gallery/img-1' && request.method() === 'POST') {
      await route.fulfill(json({ job_id: 'job-edited', status: 'queued', stage: 'queued', operation: 'edit' }, 202));
      return;
    }
    if (url.pathname === '/api/edits' && request.method() === 'POST') {
      if (options.editUploadFailure) {
        await route.fulfill(json({ detail: 'Upload image is required.' }, 422));
        return;
      }
      await route.fulfill(json({ job_id: 'job-upload-edited', status: 'queued', stage: 'queued', operation: 'edit' }, 202));
      return;
    }
    if (url.pathname === '/api/generate/jobs') {
      const includeFinished = url.searchParams.get('include_finished') === 'true';
      await route.fulfill(json(includeFinished ? historyJobs : runningJobs));
      return;
    }
    if (url.pathname === '/api/generate/jobs/events') {
      await route.fulfill({ status: 204 });
      return;
    }
    if (url.pathname === '/api/generate/job-generated/events') {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `event: job\ndata: ${JSON.stringify(job('job-generated', 'browser smoke prompt'))}\n\n`
      });
      return;
    }
    if (url.pathname === '/api/generate/job-edited/events') {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `event: job\ndata: ${JSON.stringify({ ...job('job-edited', 'browser edit prompt'), operation: 'edit' })}\n\n`
      });
      return;
    }
    if (url.pathname === '/api/generate/job-upload-edited/events') {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `event: job\ndata: ${JSON.stringify({ ...job('job-upload-edited', 'browser upload edit prompt'), operation: 'edit' })}\n\n`
      });
      return;
    }
    if (url.pathname.startsWith('/api/generate/job-')) {
      const id = url.pathname.split('/').pop() || 'job-generated';
      await route.fulfill(json(job(id, 'polled prompt')));
      return;
    }

    await route.continue();
  });
}

async function loadApp(page: Page, options: MockOptions = {}) {
  await mockApi(page, options);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Prompt' })).toBeVisible();
}

test('access gate unlocks before loading the app', async ({ page }) => {
  await mockApi(page, { authenticated: false });
  await page.goto('/');

  await expect(page.getByRole('heading', { name: 'Access Key' })).toBeVisible();
  await page.getByLabel('Access Key').fill('open-sesame');
  await page.getByRole('button', { name: 'Unlock' }).click();

  await expect(page.getByRole('heading', { name: 'Prompt' })).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Prompt', exact: true })).toBeVisible();
});

test('settings drawer traps focus and key form controls have accessible names', async ({ page }) => {
  await loadApp(page);

  await expect(page.getByRole('textbox', { name: 'Model' })).toHaveValue('preset-default-model');
  await page.getByRole('button', { name: 'Settings' }).click();
  const drawer = page.getByRole('dialog', { name: 'Settings' });
  await expect(drawer).toBeVisible();
  await expect(page.getByLabel('API URL')).toHaveValue('https://api.example.com');
  await expect(page.getByLabel('Default model')).toHaveValue('preset-default-model');
  await expect(page.getByLabel('Filter prompt')).toBeVisible();

  for (let index = 0; index < 12; index += 1) {
    await page.keyboard.press('Tab');
    await expect.poll(() => drawer.evaluate((node) => node.contains(document.activeElement))).toBe(true);
  }

  await page.keyboard.press('Escape');
  await expect(drawer).toBeHidden();
});

test('generation, gallery edit source, batch favorite, and lightbox flows work with mocked API', async ({ page }) => {
  await loadApp(page);

  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('browser smoke prompt');
  await page.getByRole('button', { name: 'Generate', exact: true }).click();
  await expect(page.getByRole('img', { name: 'Generated preview' })).toBeVisible();

  await page.locator('.gallery-card').first().getByRole('button', { name: 'Edit' }).click();
  await expect(page.getByRole('status')).toContainText('Gallery image ready for edits');
  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('browser edit prompt');
  await page.getByRole('button', { name: 'Edits' }).click();
  await expect(page.getByRole('img', { name: 'Generated preview' })).toBeVisible();

  await page.getByLabel('Filter prompt').fill('First');
  await expect(page.getByRole('img', { name: 'First gallery image' })).toBeVisible();

  await page.getByRole('button', { name: 'Select' }).click();
  await page.getByRole('button', { name: 'Select page' }).click();
  await page.getByRole('button', { name: 'Favorite selected', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Updated');

  await page.getByRole('button', { name: 'Cancel selection' }).click();
  await page.getByRole('img', { name: 'First gallery image' }).click();
  const lightbox = page.getByRole('dialog', { name: 'Image Details' });
  await expect(lightbox).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(lightbox).toBeHidden();
});

test('gallery url state restores filters, lightbox, and job history tab', async ({ page }) => {
  await mockApi(page);
  await page.goto('/?prompt=Second&favorite=true&image=img-2&jobs=history');

  await expect(page.getByLabel('Filter prompt')).toHaveValue('Second');
  await expect(page).toHaveURL(/prompt=Second/);
  await expect(page).toHaveURL(/favorite=true/);

  const lightbox = page.getByRole('dialog', { name: 'Image Details' });
  await expect(lightbox).toBeVisible();
  await expect(lightbox).toContainText('img-2.png');
  await expect(page).toHaveURL(/image=img-2/);

  await page.keyboard.press('Escape');
  await expect(lightbox).toBeHidden();
  await expect(page).not.toHaveURL(/image=img-2/);

  const jobsDrawer = page.getByRole('dialog', { name: 'Job History' });
  await expect(jobsDrawer).toBeVisible();
  await expect(jobsDrawer.getByText('saved prompt')).toBeVisible();
  await expect(page).toHaveURL(/jobs=history/);

  await page.getByLabel('Filter prompt').fill('First');
  await expect(page).toHaveURL(/prompt=First/);
});

test('single image delete uses custom confirmation and can be undone before the server delete', async ({ page }) => {
  const deleteRequests: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (request.method() === 'DELETE' && url.pathname === '/api/gallery/img-1') deleteRequests.push(url.pathname);
  });
  await loadApp(page);

  await page.locator('.gallery-card').first().getByRole('button', { name: 'Delete' }).click();
  const confirmDialog = page.getByRole('dialog', { name: 'Delete image?' });
  await expect(confirmDialog).toBeVisible();
  await expect(confirmDialog).toContainText('5 seconds');

  await confirmDialog.getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByRole('status')).toContainText('Image will be deleted in 5 seconds');
  await expect(page.getByRole('img', { name: 'First gallery image' })).toBeHidden();

  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(page.getByRole('status')).toContainText('Image deletion undone');
  await expect(page.getByRole('img', { name: 'First gallery image' })).toBeVisible();
  await page.waitForTimeout(5200);
  expect(deleteRequests).toHaveLength(0);
});

test('delete all requires typed confirmation before submitting', async ({ page }) => {
  const deleteAllRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return request.method() === 'DELETE' && url.pathname === '/api/gallery';
  });
  await loadApp(page);

  await page.getByRole('button', { name: 'Delete All' }).click();
  const confirmDialog = page.getByRole('dialog', { name: 'Delete all gallery images?' });
  await expect(confirmDialog).toBeVisible();
  await expect(confirmDialog.getByRole('button', { name: 'DELETE' })).toBeDisabled();

  await confirmDialog.getByRole('textbox').fill('DELETE');
  await expect(confirmDialog.getByRole('button', { name: 'DELETE' })).toBeEnabled();
  await confirmDialog.getByRole('button', { name: 'DELETE' }).click();
  await deleteAllRequest;
  await expect(page.getByRole('status')).toContainText('All server images deleted');
});

test('uploaded edit sources append, submit, and clear', async ({ page }) => {
  await loadApp(page);

  const upload = page.getByLabel('Upload edit image');
  await upload.setInputFiles([{ name: 'first.png', mimeType: 'image/png', buffer: PNG_BYTES }]);
  await expect(page.getByRole('button', { name: /Upload · first\.png/ })).toBeVisible();

  await upload.setInputFiles([{ name: 'second.png', mimeType: 'image/png', buffer: PNG_BYTES }]);
  await expect(page.getByRole('button', { name: /Upload · first\.png/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /Upload · second\.png/ })).toBeVisible();

  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('browser upload edit prompt');
  const editRequestPromise = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/edits');
  await page.getByRole('button', { name: 'Edits' }).click();
  const editRequest = await editRequestPromise;
  const body = editRequest.postDataBuffer()?.toString('latin1') || '';
  expect(body).toContain('name="image[]"');
  expect(body).toContain('filename="first.png"');
  expect(body).toContain('filename="second.png"');

  await page.getByRole('button', { name: 'Clear edit sources' }).click();
  await expect(page.getByRole('button', { name: /Upload · first\.png/ })).toBeHidden();
  await expect(page.getByRole('button', { name: /Upload · second\.png/ })).toBeHidden();
  await page.getByRole('button', { name: 'Edits' }).click();
  await expect(page.getByText('Please upload an image or choose one from gallery first')).toBeVisible();
});

test('failed edit submit clears the temporary queued preview', async ({ page }) => {
  await loadApp(page, { editUploadFailure: true });

  await page.getByLabel('Upload edit image').setInputFiles([{ name: 'source.png', mimeType: 'image/png', buffer: PNG_BYTES }]);
  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('browser failed edit prompt');
  await page.getByRole('button', { name: 'Edits' }).click();

  await expect(page.getByText('Upload image is required. (422)')).toBeVisible();
  await expect(page.getByText('Queued', { exact: true })).toBeHidden();
});

test('gallery edit source can be combined with uploaded references', async ({ page }) => {
  await loadApp(page);

  await page.getByLabel('Upload edit image').setInputFiles([{ name: 'extra.png', mimeType: 'image/png', buffer: PNG_BYTES }]);
  await page.locator('.gallery-card').first().getByRole('button', { name: 'Edit' }).click();
  await expect(page.getByRole('button', { name: /Gallery · Gallery: img-1\.png/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /Upload · extra\.png/ })).toBeVisible();

  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('browser edit prompt');
  const editRequestPromise = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/edits/from-gallery/img-1');
  await page.getByRole('button', { name: 'Edits' }).click();
  const editRequest = await editRequestPromise;
  const body = editRequest.postDataBuffer()?.toString('latin1') || '';
  expect(body).toContain('filename="extra.png"');
});

test('job drawer open baseline with 500 running rows', async ({ page }) => {
  test.skip(process.env.RUN_PERFORMANCE_TESTS !== 'true', 'set RUN_PERFORMANCE_TESTS=true to run performance baselines');
  await loadApp(page, { runningJobs: manyJobs(500) });

  const startedAt = await page.evaluate(() => performance.now());
  await page.getByRole('button', { name: 'Job History' }).click();
  await expect(page.getByRole('dialog', { name: 'Job History' })).toBeVisible();
  await expect(page.getByText('history prompt 499')).toBeVisible();
  const elapsedMs = await page.evaluate((start) => performance.now() - start, startedAt);

  expect(elapsedMs).toBeLessThan(500);
});
