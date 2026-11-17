import { test, expect } from '@playwright/test'

test.afterEach(async ({ request }) => {
  const headers = { Authorization: 'Bearer e2e-synthetic-api-key-00000000000000000000' }
  for (const kind of ['subscriptions', 'chains', 'entries', 'exits', 'subscriptions', 'sources']) {
    const response = await request.get(`/api/v1/${kind}`, { headers })
    for (const row of await response.json()) {
      if (/^(测试|链路测试)/.test(row.name)) await request.delete(`/api/v1/${kind}/${row.id}`, { headers })
    }
  }
})

test('链路入口支持自动与指定切换、保存回显和状态展示', async ({ page, request }) => {
  const headers = { Authorization: 'Bearer e2e-synthetic-api-key-00000000000000000000' }
  async function post(path: string, data: unknown) {
    const response = await request.post('/api/v1' + path, { headers, data })
    expect(response.ok()).toBeTruthy()
    return response.json()
  }
  const source = await post('/sources/file', {
    name: '链路测试多入口源', content: 'proxies:\n' +
      '  - {name: entry-one, type: http, server: one.example.invalid, port: 8080}\n' +
      '  - {name: entry-two, type: http, server: two.example.invalid, port: 8080}'
  })
  const subscription = await post('/subscriptions', { name: '链路测试入口订阅', source_ids: [source.id] })
  const entry = await post('/entries', { name: '链路测试候选', subscription_id: subscription.id })
  const exit = await post('/exits', { name: '链路测试出口', server: 'exit.example.invalid', port: 8080 })
  const chain = await post('/chains', { name: '链路测试选择', entry_id: entry.id, exit_id: exit.id, relay_enabled: false })
  const nodes = await (await request.get(`/api/v1/entries/${entry.id}/nodes`, { headers })).json()
  await page.goto('/login')
  await page.getByLabel('管理员账号').fill('admin')
  await page.getByLabel('密码', { exact: true }).fill('test-password-12345')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByRole('link', { name: '链式', exact: true }).click()
  const card = page.locator('.chain-card').filter({ has: page.getByRole('heading', { name: chain.name, exact: true }) })
  await expect(card).toContainText('自动测速选择')
  await expect(card).toContainText('当前入口由客户端测速选择')
  await card.getByRole('button', { name: '编辑', exact: true }).click()
  const form = page.getByRole('dialog', { name: '编辑链路', exact: true })
  await form.getByText('指定入口', { exact: true }).click()
  await expect(form.getByRole('button', { name: '保存配置' })).toBeDisabled()
  const picker = form.getByRole('combobox', { name: '指定入口节点', exact: true })
  await expect(picker).toBeEnabled()
  await picker.press('Enter')
  await page.getByRole('option', { name: new RegExp(nodes[1].server) }).click()
  await form.getByRole('button', { name: '保存配置' }).click()
  await expect(form).not.toBeVisible()
  await expect(card.locator('.chain-selection')).toContainText(nodes[1].name)
  await page.reload()
  await expect(card.locator('.chain-selection')).toContainText('指定节点：' + nodes[1].name)
  await card.getByRole('button', { name: '编辑', exact: true }).click()
  await expect(form.getByRole('radio', { name: '指定入口', exact: true })).toBeChecked()
  await expect(form).toContainText(nodes[1].name)
  await expect(form.getByRole('button', { name: '保存配置' })).toBeEnabled()
  await form.getByRole('button', { name: '取消', exact: true }).click()

  // Live core state must be distinguished from the last probe's fastest entry.
  let currentEntry = nodes[0].name
  await page.route('**/api/v1/chains', async route => {
    const response = await route.fetch()
    const rows = await response.json()
    await route.fulfill({ json: rows.map((row: { id: string }) => row.id === chain.id ? {
      ...row, relay_enabled: true, status: { state: 'running', current_entry: currentEntry, latency: 28 },
      probe: { reachable: true, latency: 20, entry: nodes[1].name, error: null, tested_at: Date.now() / 1000 }
    } : row) })
  })
  await page.getByRole('button', { name: '刷新状态', exact: true }).click()
  await expect(card.locator('.chain-selection')).toContainText('中转当前入口：' + nodes[0].name)
  await expect(card.getByRole('status')).toContainText('本次检测指定入口：' + nodes[1].name)
  currentEntry = nodes[1].name
  await expect(card.locator('.chain-selection')).toContainText('中转当前入口：' + nodes[1].name, { timeout: 10000 })
  await page.unroute('**/api/v1/chains')
  await page.getByRole('button', { name: '刷新状态', exact: true }).click()
  await expect(card.locator('.chain-selection')).toContainText('客户端更新订阅后使用指定入口')

  for (const width of [320, 390, 768]) {
    await page.setViewportSize({ width, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  }
  await card.getByRole('button', { name: '编辑', exact: true }).click()
  await form.getByText('自动测速选择', { exact: true }).click()
  await form.getByRole('button', { name: '保存配置' }).click()
  await expect(form).not.toBeVisible()
  await expect(card.locator('.chain-selection')).toContainText('自动测速选择')
  const saved = await (await request.get('/api/v1/chains', { headers })).json()
  expect(saved.find((row: { id: string }) => row.id === chain.id)).toMatchObject({ entry_mode: 'auto', entry_node_key: null })
})

test('入口出口配置、原生订阅、连接信息与移动端', async ({ page, request }) => {
  const headers = { Authorization: 'Bearer e2e-synthetic-api-key-00000000000000000000' }
  const sourceResponse = await request.post('/api/v1/sources/file', { headers, data: {
    name: '链路测试入口源', content: 'proxies:\n  - {name: test-entry, type: http, server: example.invalid, port: 8080}'
  } })
  expect(sourceResponse.ok()).toBeTruthy()
  const source = await sourceResponse.json()
  const subResponse = await request.post('/api/v1/subscriptions', { headers, data: {
    name: '链路测试入口订阅', source_ids: [source.id]
  } })
  expect(subResponse.ok()).toBeTruthy()
  await page.goto('/login')
  await page.getByLabel('管理员账号').fill('admin')
  await page.getByLabel('密码', { exact: true }).fill('test-password-12345')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByRole('link', { name: '链式', exact: true }).click()
  await page.getByRole('tab', { name: '入口', exact: true }).click()
  await page.getByRole('button', { name: '添加入口' }).first().click()
  await page.getByLabel('配置名称').fill('测试入口')
  await page.getByRole('combobox', { name: '入口来源订阅', exact: true }).press('Enter')
  await page.getByRole('option', { name: '链路测试入口订阅' }).click()
  await page.getByRole('button', { name: '保存配置' }).click()
  await expect(page.getByText('测试入口', { exact: true })).toBeVisible()
  await page.getByRole('tab', { name: '出口', exact: true }).click()
  await page.getByRole('button', { name: '添加出口' }).first().click()
  await page.getByLabel('配置名称').fill('测试出口')
  await page.getByText('YAML 导入', { exact: true }).click()
  await page.getByLabel('出口 YAML').fill('- {name: synthetic, type: http, server: exit.example.invalid, port: 80, username: test, password: synthetic-only}')
  await page.getByRole('button', { name: '保存配置' }).click()
  await expect(page.getByText('测试出口', { exact: true })).toBeVisible()
  await page.getByRole('tab', { name: '链路', exact: true }).click()
  await page.getByRole('button', { name: '添加链路' }).first().click()
  await page.getByLabel('配置名称').fill('测试两跳')
  await page.getByRole('combobox', { name: '链路入口', exact: true }).press('Enter')
  await page.getByRole('option', { name: '测试入口', exact: true }).click()
  await page.getByRole('combobox', { name: '链路出口', exact: true }).press('Enter')
  await page.getByRole('option', { name: '测试出口', exact: true }).click()
  await page.getByRole('button', { name: '保存配置' }).click()
  await expect(page.getByText('测试两跳', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '连接信息', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '代理连接信息' })).toContainText('SOCKS5 URI')
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: '更多操作', exact: true }).click()
  await page.getByRole('menuitem', { name: '生成订阅', exact: true }).click()
  await expect(page).toHaveURL(/\/subscriptions$/)
  const card = page.locator('.sub-card').filter({ has: page.getByRole('heading', { name: '测试两跳 订阅', exact: true }) })
  await card.getByRole('button', { name: '更多操作' }).click()
  await page.getByRole('menuitem', { name: '预览 / 下载' }).click()
  await expect(page.locator('.config-preview')).toContainText('dialer-proxy:')
  await page.keyboard.press('Escape')
  await page.goto('/chains')
  for (const width of [320, 390, 768]) {
    await page.setViewportSize({ width, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  }
})
