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
