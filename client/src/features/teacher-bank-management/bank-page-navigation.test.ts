import {describe,expect,it,vi} from 'vitest'
const route=vi.hoisted(()=>({path:undefined as string|undefined}))
vi.mock('vue-router',()=>({useRoute:()=>route}))
import {useBankPageUrl} from './bank-page-navigation'
describe('bank page navigation context',()=>{
 it.each([
 ['/teacher/courses/7/banks/9/','/manage/resources/courses/7/banks/9/'],
 ['/teacher/courses/7/banks/9/questions/3/edit/?group=4#material','/manage/resources/courses/7/banks/9/questions/3/edit/?group=4#material'],
 ['/teacher/banks/9/import/','/manage/resources/banks/9/import/'],
 ['/teacher/courses/7/banks/?tab=course-kcs','/manage/resources/?tab=concepts'],
 ['/teacher/banks/','/manage/resources/'],
 ['/teacher/courses/7/banks/shared/','/manage/resources/?visibility=shared'],
 ['/teacher/courses/7/banks/archived/?q=test#banks','/manage/resources/?q=test&visibility=archived#banks'],
 ['/teacher/banks/shared/','/manage/resources/?visibility=shared'],
 ['/teacher/courses/','/manage/courses/'],
 ['/teacher/courses/7/','/manage/courses/'],
 ])('keeps administrator navigation inside its workspace: %s',(source,expected)=>{route.path='/manage/resources/courses/7/banks/9/';expect(useBankPageUrl()(source)).toBe(expected)})
 it.each(['/api/v1/teacher/banks/9/','/api/v1/teacher/import-templates/kcs.csv','/teacher/courses/7/data/exports/','/admin/','https://example.com/teacher/banks/9/'])('does not rewrite APIs, downloads or other modules: %s',source=>{route.path='/manage/resources/banks/9/';expect(useBankPageUrl()(source)).toBe(source)})
 it.each([undefined,'/teacher/courses/7/banks/9/'])('preserves teacher routes when path is %s',path=>{route.path=path;expect(useBankPageUrl()('/teacher/banks/9/')).toBe('/teacher/banks/9/')})
 it('follows reactive route changes and preserves missing links',()=>{route.path='/teacher/banks/9/';const url=useBankPageUrl();route.path='/manage/resources/banks/9/';expect(url('/teacher/banks/9/')).toBe('/manage/resources/banks/9/');expect(url(undefined)).toBeUndefined()})
})
