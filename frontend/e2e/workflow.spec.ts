import { test, expect } from '@playwright/test'

test('完整管理流程、路由后退与移动端布局', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', e => errors.push(e.message))
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto('/')
  await page.getByLabel('管理员账号').fill('admin')
  await page.getByLabel('密码', { exact: true }).fill('test-password-12345')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('heading', { name: '连接总览', exact: true })).toBeVisible()

  await page.getByRole('button', { name: '添加账号', exact: true }).first().click()
  await page.getByRole('textbox', { name: '批量 token' }).fill(`香港主账号,${'a'.repeat(32)}\n备用账号,${'b'.repeat(32)}`)
  await page.getByRole('button', { name: '导入账号', exact: true }).click()
  await expect(page.getByText('香港主账号', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '刷新全部', exact: true }).click()
  await expect(page.locator('.node-counter').first()).toHaveText('3 节点')
  await expect(page.locator('.account-row .el-tag').first()).toHaveText('已就绪')

  await page.getByRole('link', { name: '节点列表', exact: true }).click()
  await expect(page).toHaveURL(/\/nodes$/)
  await expect(page.locator('.table-footer')).toContainText('6 个节点')
  await page.getByRole('button', { name: '测速当前列表', exact: true }).click()
  await expect(page.getByText('36.5 ms', { exact: true }).first()).toBeVisible()

  await page.getByRole('link', { name: '订阅管理', exact: true }).click()
  await expect(page).toHaveURL(/\/subscriptions$/)
  await page.getByRole('button', { name: '创建订阅', exact: true }).first().click()
  await page.getByLabel('订阅名称', { exact: true }).fill('我的 Clash 订阅')
  await page.getByRole('button', { name: '保存订阅', exact: true }).click()
  await expect(page.getByRole('heading', { name: '我的 Clash 订阅' })).toBeVisible()
  await page.locator('.sub-card').filter({ has: page.getByRole('heading', { name: '我的 Clash 订阅', exact: true }) }).getByRole('button', { name: '更多操作', exact: true }).click()
  await page.getByRole('menuitem', { name: '预览 / 下载', exact: true }).click()
  await expect(page.locator('.config-preview')).toContainText('proxies:')
  await expect(page.locator('.config-preview')).toContainText('tls: true')
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载 YAML', exact: true }).click()
  expect((await downloadPromise).suggestedFilename()).toBe('ghelper.yaml')
  await page.keyboard.press('Escape')
  await expect(page.locator('.config-preview')).not.toBeVisible()

  // 浏览器后退键应回到上一个页面，而不是退出站点。
  await page.goBack()
  await expect(page).toHaveURL(/\/nodes$/)
  await expect(page.getByRole('heading', { name: '节点列表', exact: true })).toBeVisible()

  await page.getByRole('link', { name: '连接总览', exact: true }).click()
  await page.screenshot({ path: '../test-results/overview-desktop.png', fullPage: true })

  // 直接访问子路径（刷新页面）仍然可用。
  await page.goto('/accounts')
  await expect(page.getByRole('heading', { name: '账号管理', exact: true })).toBeVisible()

  for (const width of [320, 390, 768]) {
    await page.setViewportSize({ width, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  }
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.getByText('demo@example.invalid', { exact: true }).first()).toBeVisible()
  await page.screenshot({ path: '../test-results/accounts-mobile.png', fullPage: true })

  await page.goto('/')
  await page.screenshot({ path: '../test-results/overview-mobile.png', fullPage: true })
  await page.getByRole('button', { name: '退出', exact: true }).click()
  await expect(page).toHaveURL(/\/login$/)
  await expect(page.getByRole('heading', { name: '登录管理台' })).toBeVisible()

  // 未登录访问受保护页面会跳转到登录页，并记住原地址。
  await page.goto('/settings')
  await expect(page).toHaveURL(/\/login\?redirect=/)
  expect(errors).toEqual([])
})

test('文件源刷新后定位失效跳，检测并放弃替换草稿', async ({ page, request }) => {
  const headers = { Authorization: 'Bearer e2e-synthetic-api-key-00000000000000000000' }
  async function post(path: string, data: unknown) {
    const response = await request.post('/api/v1' + path, { headers, data })
    expect(response.ok()).toBeTruthy()
    return response.json()
  }
  const node = (name: string) => '- {name: ' + name + ', type: http, server: ' + name + '.example.invalid, port: 8080}\n'
  const content = 'proxies:\n' + node('replacement')
  const source = await post('/sources/file', { name: '刷新演练', content: content + node('original') })
  const sub = await post('/subscriptions', { name: '演练候选', source_ids: [source.id] })
  const { nodes } = await (await request.get('/api/v1/subscriptions/' + sub.id + '/nodes', { headers })).json()
  const first = await post('/exits', { name: '演练首跳', server: 'first.example.invalid', port: 8080 })
  const path = (index: number) => ({ name: index ? '主路径' : '备用路径', hops: [
    { source: 'proxy', ref: first.id }, { source: 'subscription', ref: sub.id, node_key: nodes[index].key }] })
  const values = { name: '演练双路径', paths: [path(1), path(0)], lock_exit: false, relay_enabled: false, monitor_enabled: false }
  const chain = await post('/chains', values)
  const chainUrl = '/api/v1/chains/' + chain.id
  const output = await post('/subscriptions', { name: '演练导出', chain_ids: [chain.id] })
  const writes: string[] = []
  page.on('request', r => { if (r.method() === 'PUT' && r.url().endsWith(chainUrl)) writes.push(r.url()) })
  await page.goto('/sources')
  await page.getByLabel('管理员账号').fill('admin')
  await page.getByLabel('密码', { exact: true }).fill('test-password-12345')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  const card = page.locator('article').filter({ has: page.getByRole('heading', { name: source.name, exact: true }) })
  const chooser = page.waitForEvent('filechooser')
  await card.getByRole('button', { name: '重新上传' }).click()
  await (await chooser).setFiles({ name: 'updated.yaml', mimeType: 'text/yaml', buffer: Buffer.from(content) })
  await card.getByRole('button', { name: /刷新影响.*1 条链路/ }).click()
  const report = page.getByRole('dialog')
  await expect(report).toContainText('主路径受损')
  await expect(report).toContainText('第 2 跳')
  const publicConfig = await (await request.get(output.clash_url)).text()
  expect(publicConfig).toContain('replacement.example.invalid')
  await report.getByRole('link', { name: '定位此跳' }).click()
  const panel = page.getByLabel('第 2 跳配置')
  await expect(panel).toBeVisible()
  const historicUrl = page.url()
  await panel.getByRole('option', { name: /replacement/ }).click()
  const diagnostic = page.waitForRequest(r => r.url().endsWith('/chains/diagnostics') && r.method() === 'POST')
  await page.getByRole('button', { name: '检测草稿', exact: true }).click()
  expect((await diagnostic).postDataJSON().draft.paths[0].hops[1].node_key).toBe(nodes[0].key)
  await expect(page.getByRole('dialog')).toContainText('缺少 Mihomo 内核')
  await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).last().click()
  await page.getByRole('button', { name: '返回链路' }).click()
  await page.getByRole('button', { name: '放弃修改' }).click()
  await expect(page).toHaveURL(/\/chains$/)
  expect(writes).toEqual([])
  const saved = await (await request.get(chainUrl, { headers })).json()
  expect(saved.revision).toBe(chain.revision)
  expect(saved.paths[0].hops[1].node_key).toBe(nodes[1].key)
  expect(await (await request.get(output.clash_url)).text()).toBe(publicConfig)

  const updated = await request.put(chainUrl, { headers, data: { ...values, paths: [path(0)], revision: saved.revision } })
  expect(updated.ok()).toBeTruthy()
  await page.goto(historicUrl)
  await expect(page.getByText(/报告定位已过期/)).toBeVisible()
  await expect(page.getByRole('button', { name: '保存链路' })).toHaveCount(0)
  const outside = new URL(historicUrl)
  outside.searchParams.set('revision', String(saved.revision + 1))
  outside.searchParams.set('hop', '99')
  await page.goto(outside.href)
  await expect(page.getByText(/报告定位已过期/)).toBeVisible()
  await request.delete('/api/v1/subscriptions/' + output.id, { headers })
  expect((await request.delete(chainUrl, { headers })).ok()).toBeTruthy()
  await page.goto(historicUrl)
  await expect(page.getByText(/无法定位报告中的链路/)).toBeVisible()
})
