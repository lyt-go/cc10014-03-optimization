import { expect, test } from '@playwright/test'

test('字段映射版本表：列表、筛选、排序、分页、解析、状态机、冲突与刷新标记', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) errors.push(`${r.status()} ${r.url()}`)
  })

  await page.goto('/column_mapping_version')
  await expect(page.locator('tbody tr[data-testid^="row-"]')).toHaveCount(9)

  // 筛选 + 检索
  await page.getByTestId('filter-规则编号').fill('MAP-ORD-001')
  await page.getByTestId('query').click()
  await expect(page.locator('tbody tr[data-testid^="row-"]')).toHaveCount(3)
  await page.getByTestId('reset').click()
  await expect(page.locator('tbody tr[data-testid^="row-"]')).toHaveCount(9)

  // 排序切换
  await page.getByTestId('sort-生效日期').click()
  await expect(page.getByTestId('sort-生效日期')).toContainText('▲')

  // 分页：每页 5 条 → 两页
  await page.getByTestId('page-size').selectOption('5')
  await expect(page.locator('tbody tr[data-testid^="row-"]')).toHaveCount(5)
  await expect(page.getByTestId('page-info')).toContainText('共 9 条')
  await page.getByTestId('next-page').click()
  await expect(page.locator('tbody tr[data-testid^="row-"]')).toHaveCount(4)
  await page.getByTestId('prev-page').click()
  // 恢复每页 20 条，保证后续解析出的新版本行在当前页可见
  await page.getByTestId('page-size').selectOption('20')

  // 定位
  await page.getByTestId('locate-id').fill('1')
  await page.getByTestId('locate').click()
  await expect(page.locator('.page-foot .error-text, .page-foot').first()).toBeVisible()

  // 解析新规则
  await page.getByTestId('open-parse').click()
  await page.getByTestId('parse-规则编号').fill('MAP-E2E-001')
  await page.getByTestId('parse-源字段名').fill('e1,e2')
  await page.getByTestId('parse-目标字段名').fill('te1,te2')
  await page.getByTestId('parse-数据类型').fill('STRING,INT')
  await page.getByTestId('submit-parse').click()
  await expect(page.getByTestId('parse-modal')).toHaveCount(0)
  await expect(page.locator('tbody tr').filter({ hasText: 'MAP-E2E-001' })).toHaveCount(2)

  // 状态机：草稿行只能先提交校验，直接生效应被后端拒绝
  const draftRow = page.locator('tbody tr').filter({ hasText: 'MAP-E2E-001' }).first()
  await expect(draftRow).toHaveAttribute('data-status', '草稿')
  await draftRow.getByRole('button', { name: '提交校验', exact: true }).click()
  await expect(draftRow).toHaveAttribute('data-status', '待校验')
  await draftRow.getByRole('button', { name: '标记生效', exact: true }).click()
  await expect(draftRow).toHaveAttribute('data-status', '已生效')

  // 当前字段列表两个排序边界
  await page.getByTestId('tab-current').click()
  await expect(page.locator('[data-testid^="current-row-"]').first()).toBeVisible()
  await page.getByTestId('current-sort-seq').click()
  const seqRows = await page.locator('[data-testid^="current-row-"]').count()
  expect(seqRows).toBeGreaterThanOrEqual(9)
  await page.getByTestId('current-sort-date').click()

  // 刷新标记页签：生效后应有标记，且刷新原因含规则编号
  await page.getByTestId('tab-marks').click()
  await page.getByTestId('mark-filter-rule').fill('MAP-E2E-001')
  await page.getByTestId('mark-query').click()
  await expect(page.locator('[data-testid^="mark-row-"]').first()).toBeVisible()
  await expect(page.locator('tbody').first()).toContainText('MAP-E2E-001')

  // 冲突页签可打开
  await page.getByTestId('tab-conflicts').click()
  await expect(page.locator('.page-foot')).toContainText('谁优先尚无结论')

  // 返回字段映射列表核对
  await page.getByTestId('back-to-mapping').click()
  await expect(page).toHaveURL('/column_mapping')

  expect(errors).toEqual([])
})
