import {mount,flushPromises} from '@vue/test-utils'
import {beforeEach,describe,expect,it,vi} from 'vitest'
const mocks=vi.hoisted(()=>({get:vi.fn(),post:vi.fn(),route:{query:{}}}))
vi.mock('./api',async original=>({...await original<typeof import('./api')>(),getAdminApi:()=>mocks}))
vi.mock('vue-router',()=>({useRoute:()=>mocks.route}))
import AdminUsers from './AdminUsers.vue'
import AdminCourses from './AdminCourses.vue'
import AdminCatalog from './AdminCatalog.vue'
const global={stubs:{SiteDialog:true,AdminClassDialog:true,AdminTaskDialog:true,AdminBankDialog:true,AdminExportDialog:true,AdminQuestionDialog:true,AdminConceptDialog:true}}
const rows=(role='teacher')=>Array.from({length:45},(_,i)=>({id:i+1,name:`${role}-${i+1}`,username:`${role}${i+1}`,role,is_active:true,course_id:1,course_name:'课程',visibility:'private',status:'active',term:'2026'}))
describe('administrator list pagination',()=>{
 beforeEach(()=>{vi.clearAllMocks()})
 it('paginates accounts by 20 and resets page on search and role changes',async()=>{mocks.get.mockResolvedValue({items:[...rows(),...rows('student')]});const w=mount(AdminUsers,{global});await flushPromises();expect(w.findAll('.admin-list-table tbody tr')).toHaveLength(20);await w.get('[data-next]').trigger('click');expect(w.get('tbody').text()).toContain('teacher-21');await w.get('input[aria-label="搜索账号"]').setValue('teacher45');expect(w.findAll('tbody tr')).toHaveLength(1);expect(w.get('tbody').text()).toContain('teacher-45');await w.get('input[aria-label="搜索账号"]').setValue('');expect(w.get('tbody').text()).toContain('teacher-1');await w.get('[data-next]').trigger('click');await w.findAll('.tabs button')[1]!.trigger('click');expect(w.get('tbody').text()).toContain('student-1');expect(w.get('tbody').text()).not.toContain('student-21')})
 it('uses the same columns and resets course pagination when switching to classes',async()=>{mocks.get.mockResolvedValue({items:rows()});const w=mount(AdminCourses,{global});await flushPromises();expect(w.findAll('.admin-list-table col')).toHaveLength(5);expect(w.findAll('tbody tr')).toHaveLength(20);await w.get('[data-next]').trigger('click');expect(w.get('tbody').text()).toContain('teacher-21');await w.findAll('.tabs button')[1]!.trigger('click');expect(w.get('tbody').text()).toContain('teacher-1');expect(w.get('tbody').text()).not.toContain('teacher-21')})
 it.each(['bank','data','audit'] as const)('paginates every %s catalog and resets filters',async section=>{mocks.get.mockResolvedValue({banks:rows(),questions:rows(),concepts:rows(),experiments:rows(),tasks:rows(),exports:rows(),purge_requests:rows(),items:rows()});const w=mount(AdminCatalog,{props:{section},global});await flushPromises();expect(w.findAll('.admin-list-table col')).toHaveLength(5);expect(w.findAll('tbody tr')).toHaveLength(20);await w.get('[data-next]').trigger('click');expect(w.findAll('tbody tr')).toHaveLength(20);await w.get('input').setValue('teacher-45');expect(w.findAll('tbody tr')).toHaveLength(1);expect(w.find('[data-next]').exists()).toBe(false)})
})
