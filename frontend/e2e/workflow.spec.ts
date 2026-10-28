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
  await page.getByRole('button', { name: '预览 / 下载', exact: true }).click()
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
