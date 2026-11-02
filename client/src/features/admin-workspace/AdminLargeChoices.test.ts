import {mount,flushPromises} from '@vue/test-utils'
import {beforeEach,describe,expect,it,vi} from 'vitest'
const mocks=vi.hoisted(()=>({get:vi.fn(),post:vi.fn()}))
vi.mock('./api',async original=>({...await original<typeof import('./api')>(),getAdminApi:()=>mocks}))
vi.mock('vue-router',()=>({useRoute:()=>({query:{}})}))
import AdminCatalog from './AdminCatalog.vue'
import AdminCourses from './AdminCourses.vue'
import SiteSelect from '@/features/management-shared/SiteSelect.vue'
const global={stubs:{SiteDialog:{props:['open'],template:'<section v-if="open"><slot/><slot name="footer"/></section>'},AdminClassDialog:true,AdminTaskDialog:true,AdminBankDialog:true,AdminExportDialog:true,AdminQuestionDialog:true,AdminConceptDialog:true,AdminRecycleDialog:true}}
describe('administrator large choice fields',()=>{
 beforeEach(()=>vi.clearAllMocks())
 it('uses a searchable course filter and keeps the chosen course filter applied',async()=>{
  mocks.get.mockResolvedValue({banks:[{id:1,name:'甲题库',course_id:10,course_name:'甲课程'},{id:2,name:'乙题库',course_id:20,course_name:'乙课程'}]})
  const w=mount(AdminCatalog,{props:{section:'bank'},global});await flushPromises()
  const select=w.findAllComponents(SiteSelect).find(c=>c.props('label')==='筛选课程')!
  expect(select).toBeDefined();expect(select.props('searchable')).toBe(true)
  select.vm.$emit('update:modelValue','20');await flushPromises()
  expect(w.get('tbody').text()).toContain('乙题库');expect(w.get('tbody').text()).not.toContain('甲题库')
 })
 it('offers searchable teacher choices in course creation',async()=>{
  mocks.get.mockResolvedValue({items:[{id:2,name:'教师',username:'teacher',role:'teacher',is_active:true}]})
  const w=mount(AdminCourses,{global});await flushPromises()
  await w.findAll('button').find(b=>b.text().includes('新建课程'))!.trigger('click')
  const select=w.findAllComponents(SiteSelect).find(c=>c.props('label')==='负责人')!
  expect(select).toBeDefined();expect(select.props('searchable')).toBe(true)
 })
})
