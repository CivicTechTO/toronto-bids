import { Pipe, PipeTransform } from '@angular/core';
import { DatePipe } from '@angular/common';

@Pipe({
  name: 'dateOnly'
})
export class DateOnlyPipe implements PipeTransform {
  transform(value: any, format: string = 'yyyy-MM-dd'): any {
    const datePipe = new DatePipe('en-US');
    const transformedDate = datePipe.transform(value, format);
    return transformedDate;
  }
}
