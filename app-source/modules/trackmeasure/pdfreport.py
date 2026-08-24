import fpdf
from calibration import *
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import cv2
import time
import math
import os
import tempfile
import shutil
from PIL import Image
import copy

dir_path = os.path.dirname(os.path.abspath(__file__))

class PDF(fpdf.FPDF):
    def header(self):
        p = os.path.join(dir_path, 'images', 'report_header.jpg')
        self.image(p, x=0, y=5, w=210)

    def footer(self):
        f = os.path.join(dir_path, 'images', 'report_footer.jpg')
        self.image(f, x=10, y=280, w=70)
        self.set_font('Helvetica', 'i', 10)
        self.set_y(-15)
        self.set_x(150)
        self.cell(50, 10, f'Page {self.page_no()}', align='C')

class PDFReport:
    def __init__(self, track_data, metadata, cine_path):
        #make temp dir for files
        self.temp_dir = os.path.join(tempfile.gettempdir(), 'PCA_report_temp')
        os.makedirs(self.temp_dir, exist_ok=True)

        # init vars
        self.data = track_data
        self.info = metadata
        self.info['units'] = self.info['units'].replace('u', 'µ')
        self.info['time_units'] = self.info['time_units'].replace('u', 'µ')
        self.cine_path = cine_path
        self.pdf = PDF()
        self.cal = Calibration()

    def __del__(self):
        self._delete_temp_files()

    def _delete_temp_files(self):
        try:
            shutil.rmtree(self.temp_dir)
        except: pass

    def plot_img(self, type, idx=None, current_point=None, size=160):
        if type=='template':
            int_point = tuple(int(x) for x in current_point)
            name = f'temp_img_{idx}'
            b_sz = int(size/4)
            sz = (size, size)
            if 'imgs' in self.info and idx < len(self.info['imgs']) and self.info['imgs'][idx] is not None:
                img = self.info['imgs'][idx]
            else:
                logging.warning(f"Template image not found for index {idx}")
                return
            img_border = cv2.copyMakeBorder(img, b_sz, b_sz, b_sz, b_sz, cv2.BORDER_CONSTANT, value=[0,0,0])
            img_crop = img_border[int_point[1]: int_point[1] + int(size/2), int_point[0]: int_point[0] + int(size/2)]
            img = cv2.resize(img_crop, sz, interpolation=cv2.INTER_CUBIC)
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img_final = cv2.copyMakeBorder(img_rgb, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=[130, 130, 130])
        img_p = os.path.join(self.temp_dir, f'{name}.png')
        cv2.imwrite(img_p, img_final)

    def plot_graph(self, x, y, attr, idx, notes, n_diff, peaks=None):
        plt.rcParams['figure.figsize'] = (15, 5)
        plt.rcParams['font.size'] = 12
        ax = plt.gca()
        plt.grid(True)
        if 'vib' in attr:
            plt.plot(x, y)
            colors = ['red', 'orange']
            for i, peak in enumerate(peaks):
                plt.scatter(x[peak], y[peak], color=colors[i], label=f"{x[peak]:.2f} Hz", s=100)
            if len(peaks) > 0:
                plt.legend(title='Largest Peaks', loc='upper right')
            plt.title(f"{attr[0]}-Frequency Spectrum", fontsize=16, weight='bold')
        else:
            labels = [f'x-{attr}', f'y-{attr}', f'{attr}-mag']
            tableau_colors = list(mcolors.TABLEAU_COLORS.values())
            line_colors = tableau_colors[:len(y)] 
            for y_val, lbl, plot_color in zip(y, labels, line_colors):
                plt.plot(x, y_val, 'D-', label=lbl, color=plot_color)
                if any(y_val < 0):
                    plt.axhline(0, color='black', linewidth=1)
                for i in range(len(y_val)):
                    if 'speed' in attr:
                        loc_idx = i - n_diff // 2
                    elif 'accel' in attr:
                        loc_idx = i - n_diff
                    else:
                        loc_idx = i
                    if notes and i in notes and 0 <= loc_idx < len(x):
                        xi = x[loc_idx]
                        yi = y_val[loc_idx]
                        rgb = np.array(mcolors.to_rgb(plot_color))
                        lighter_color = rgb + (np.array([1, 1, 1]) - rgb) * 0.5
                        plt.plot(xi, yi, marker='*', markersize=10, color=lighter_color, zorder=10)
                        plt.annotate(
                            notes[i],
                            (xi, yi),
                            textcoords="offset points",
                            xytext=(0, 10),
                            ha='center',
                            fontsize=8,
                            fontname='DejaVu Sans',
                            color=lighter_color,
                            bbox=dict(boxstyle="round,pad=0.3", fc="black", ec=plot_color, alpha=0.7)
                        )

            plt.legend(title=None, loc='upper right')
            ax.set_xlabel(f"Time from Trigger ({self.info['time_units']})")
            if attr == 'disp':
                plt.title(f"Displacement ({self.info['units']}) vs. Time ({self.info['time_units']})", fontsize=16, weight='bold') 
            elif attr == 'speed':
                plt.title(f"Speed ({self.info['units']}/{self.info['time_units']}) vs. Time ({self.info['time_units']})", fontsize=16, weight='bold')
            elif attr == 'accel':
                plt.title(f"Acceleration ({self.info['units']}/{self.info['time_units']}²) vs. Time ({self.info['time_units']})", fontsize=16, weight='bold')

        plt_p = os.path.join(self.temp_dir, f'{attr}_{idx}.png')
        plt.savefig(plt_p)
        plt.close()


    def generate_pdf(self, report_path):
        #Added unicode support and fallback fonts
        self.pdf.core_fonts_encoding = 'utf-8'
        self.pdf.add_font('Sans', '', os.path.join(dir_path, 'fonts', 'FreeSans.ttf'), uni=True)
        self.pdf.add_font('SansBold', '', os.path.join(dir_path, 'fonts', 'FreeSansBold.ttf'), uni=True)
        self.pdf.add_font('Gargi', '', os.path.join(dir_path, 'fonts', 'gargi.ttf'), uni=True)         # Hindi 
        #self.pdf.add_font('Firefly', '', os.path.join(dir_path, 'fonts', 'fireflysung.ttf'), uni=True) Error on Linux..# Japanese & Chinese
        self.pdf.add_font('Eunjin', '', os.path.join(dir_path, 'fonts', 'Eunjin.ttf'), uni=True)       # Korean
        self.pdf.add_font('Waree', '', os.path.join(dir_path, 'fonts', 'Waree.ttf'), uni=True)         # Thai
        self.pdf.add_font('DejaVuSans', '', os.path.join(dir_path, 'fonts', 'DejaVuSans.ttf'), uni=True)
        self.pdf.set_fallback_fonts(['Sans','DejaVuSans', 'Gargi','Eunjin','Waree'])
        self.pdf.add_page()
        center = self.pdf.w/2

        # add title
        self.pdf.set_font('SansBold','', 20)  
        self.pdf.ln(40)
        self.pdf.cell(0, 10, "Tracking Data Report", align='C')
        self.pdf.ln(10)
        
        # add date of report
        self.pdf.set_font('Sans', '', 14)
        self.pdf.set_text_color(r=128,g=128,b=128)
        today = time.strftime("%d-%b-%Y")
        self.pdf.cell(0, 10, f'{today}', align='C')
        self.pdf.set_text_color(r=0,g=0,b=0)

        # add cine name and metadata
        self.pdf.ln(35)
        self.pdf.set_font('Sans', '', 14)
        cine_name = self.cine_path.split('\\')[-1]
        self.pdf.cell(0, 10, f"Cine: {cine_name}", align='C')
        self.pdf.ln(45)
        self.pdf.set_font('SansBold', '', 18)
        self.pdf.cell(0, 10, "Metadata", align='C')
        self.pdf.ln(10)
        self.pdf.set_font('Sans', '', 12)
        for i, item in enumerate(self.info['other_md']):
            item[1] = item[1].replace('us', 'µs')
            if i % 2 == 0:
                self.pdf.ln(10)
                self.pdf.set_x(center-70)
                self.pdf.cell(50, 10, f'{item[0]}: {item[1]}', align='L')
            else:
                self.pdf.set_x(center+25)
                self.pdf.cell(50, 10, f'{item[0]}: {item[1]}', align='L')
        self.pdf.ln(10)
        self.pdf.set_x(center-70)
        self.pdf.cell(50, 10, f"Trigger Timestamp: {self.info['trigger_timestamp']}", align='L')

        # add table of contents
        self.pdf.set_font('SansBold', '', 18)
        self.pdf.add_page()
        self.pdf.ln(30)
        self.pdf.cell(0, 10, "Table of Contents", align='C')
        self.pdf.set_font('SansBold', '', 14)
        self.pdf.ln(20)
        offset = 70
        self.pdf.set_x(center - offset)
        self.pdf.cell(offset, 10, "Object", align='C')
        self.pdf.set_x(center)
        self.pdf.cell(offset, 10, "Page #", align='C')
        self.pdf.set_font('Sans', '', 14)
        p = 4 + (len(self.data)-1) // 20
        for i, temp_dict in enumerate(self.data.values()):
            if i % 20 == 0 and i != 0:
                self.pdf.add_page()
                self.pdf.ln(10)
            self.pdf.ln(10)
            self.pdf.set_x(center - offset)
            self.pdf.cell(offset, 10, temp_dict["name"], align='C')
            self.pdf.set_x(center)
            self.pdf.cell(offset, 10, str(3*i+p), align='C')

        # add image with all points
        self.pdf.add_page()
        self.pdf.set_font('SansBold', '', 18)
        self.pdf.ln(20)
        self.pdf.cell(0, 10, "Tracked Points", align='C')
        g_path = os.path.join(self.temp_dir, 'graph.png')
        with Image.open(g_path) as graph_img:
            g_width, g_height = graph_img.size
            if g_width/g_height >= 180/200:
                self.pdf.image(g_path, x=fpdf.enums.Align.C, y=55, w=180)
            else:
                self.pdf.image(g_path, x=fpdf.enums.Align.C, y=55, h=200)

        # write data and graphs for every point
        for i, t in enumerate(self.data.values()):
            temp_dict = copy.deepcopy(t)
            # object name
            self.pdf.add_page()
            self.pdf.set_font('SansBold', '', 18)
            self.pdf.ln(25)
            self.pdf.set_x(20)
            if temp_dict['name'] == f'Object {i}':
                self.pdf.cell(0, 10, f"Object {i}")
            else:
                self.pdf.cell(0, 10, f"Object {i}: {temp_dict['name']}")

            # add properties
            self.pdf.ln(15)
            self.pdf.set_font('Sans', '', 14)
            self.pdf.set_x(20)
            self.pdf.cell(0, 10, f"Image scale: {self.info['scale']:.7g} {self.info['units']}/px")
            
            # Add relative object info if it exists
            if 'relative_to' in temp_dict and temp_dict['relative_to'] is not None:
                rel_id = temp_dict['relative_to']
                # Get the relative object from the main dictionary (self.data)
                rel_obj = self.data[rel_id]
                if rel_obj is not None:
                    self.pdf.ln(10)
                    self.pdf.set_x(20)
                    self.pdf.cell(0, 10, f"Relative to: {rel_obj['name']} [{rel_id}]")
            
            # add object template, table, and graphs
            # use origin frame point if available, otherwise use first point
            template_point = None
            if 'origin_frame' in temp_dict and temp_dict['origin_frame'] is not None:
                origin_frame_val = temp_dict['origin_frame']
                frame_indices = np.where(temp_dict['frames'] == origin_frame_val)[0]
                if frame_indices.size > 0:
                    template_point = tuple(temp_dict['points'][frame_indices[0]])
                else:
                    template_point = tuple(temp_dict['points'][0])
            else:
                template_point = tuple(temp_dict['points'][0])
            
            if 'imgs' in self.info and i < len(self.info['imgs']) and self.info['imgs'][i] is not None:
                self.plot_img(type='template', idx=i, current_point=template_point)
                img_p = os.path.join(self.temp_dir, f'temp_img_{i}.png')
                self.pdf.image(img_p, x=130, y=35, w=50)
            else:
                logging.warning(f"Template image not available for object {i}")
                
            self.pdf.ln(35)
            self.pdf.set_x(130)
            self.pdf.cell(50, 10, f"Template Image", align='C')
            self.pdf.ln(15)

            # add stats of interest
            # apply scaling
            for val in ['points', 'X-Displacement', 'Y-Displacement', 'Displacement', 'X-Speed', 'Y-Speed', 'Speed', 'X-Acceleration', 'Y-Acceleration', 'Acceleration']:
                temp_dict[val] = self.cal.point_transform(temp_dict[val])
            line_height = 7
            self.pdf.set_font('SansBold', '', 16)
            self.pdf.cell(0, 10, "Object Info", align='C')
            self.pdf.ln(line_height*2)
            self.pdf.set_font('Sans', '', 14)
            object_data = {}
            object_data['First Frame'] = temp_dict['frames'][0] + int(self.info['first_frame'])
            object_data['Last Frame'] = temp_dict['frames'][-1] + int(self.info['first_frame'])
           
            # Add Origin Frame
            if 'origin_frame' in temp_dict and temp_dict['origin_frame'] is not None:
                origin_frame_val = int(temp_dict['origin_frame'])
                # Find the index in frames array
                idxs = np.where(temp_dict['frames'] == origin_frame_val)[0]
                if idxs.size > 0:
                    object_data['Origin Frame'] = int(temp_dict['frames'][idxs[0]]) + int(self.info['first_frame'])
                else:
                    object_data['Origin Frame'] = int(temp_dict['frames'][0]) + int(self.info['first_frame'])
            else:
                object_data['Origin Frame'] = int(temp_dict['frames'][0]) + int(self.info['first_frame'])
           
            object_data['Time Elapsed'] = f"{temp_dict['frame_ts'][-1] - temp_dict['frame_ts'][0]:.4g} {self.info['time_units']}"
            object_data['Subpixel Algorithm'] = temp_dict['subpixel_size']
            if temp_dict['subpixel_size'] != '1.0 pix':
                object_data['Subpixel Algorithm'] += f" ({temp_dict['subpixel_type']})"
            object_data['Average Displacement'] = f"{np.mean(temp_dict['Displacement']):.4g} {self.info['units']}"
            if len(temp_dict['Speed']) > 0:
                object_data['Average Speed'] = f"{np.mean(temp_dict['Speed']):.4g} {self.info['units']}/{self.info['time_units']}"
            else:
                object_data['Average Speed'] = "N/A"
            if len(temp_dict['Acceleration']) > 0:
                object_data['Average Acceleration'] = f"{np.mean(temp_dict['Acceleration']):.4g} {self.info['units']}/{self.info['time_units']}²"
            else:
                object_data['Average Acceleration'] = "N/A"
            numeric_scores = temp_dict['scores'][temp_dict['scores'] != 'N/A']
            if len(numeric_scores) > 0:
                object_data['Median Score'] = f'{np.median(numeric_scores.astype(float)):.4g}'
            else:
                object_data['Median Score'] = "N/A"
            offset = 50
            for k, v in object_data.items():
                self.pdf.set_x(center - offset)
                self.pdf.cell(offset, line_height, k+':', align='L')
                self.pdf.set_x(center + 30)
                self.pdf.cell(offset, line_height, str(v), align='L')
                self.pdf.ln(line_height)
            
            self.pdf.ln(10)

            # speed setup
            if len(temp_dict['Speed']) > 0:
                n_diff = self.info['_n_diff']
                frames_length = len(temp_dict['frame_ts'])
                speed_offset = math.floor(n_diff/2)

            # acceleration setup
            if len(temp_dict['Acceleration']) > 0:
                n_diff = self.info['_n_diff']
                frames_length = len(temp_dict['frame_ts'])
                accel_offset = 2 * math.floor(n_diff/2)

            if temp_dict['notes'] != {}:
                # Table column headers
                col_headers = [
                    "Note",
                    "Frame",
                    f"Time from Trig ({self.info['time_units']})",
                    f"Disp ({self.info['units']})",
                    f"Speed ({self.info['units']}/{self.info['time_units']})",
                    f"Accel ({self.info['units']}/{self.info['time_units']}²)"
                ]
                col_widths = [40, 25, 35, 30, 30, 30]
                notes_table = []
                for idx, note in temp_dict['notes'].items():
                    frame = f"{temp_dict['frames'][idx] + int(self.info['first_frame'])}"
                    time_from_trig = f"{temp_dict['frame_ts'][idx]:.4g}"
                    disp = temp_dict['Displacement'][idx]
                    x_disp = temp_dict['X-Displacement'][idx]
                    y_disp = temp_dict['Y-Displacement'][idx]

                    # speed and accel need to have index shifted because of n_diff
                    if 0 <= idx - speed_offset < len(temp_dict['Speed']):
                        speed = temp_dict['Speed'][idx - speed_offset]
                        x_speed = temp_dict['X-Speed'][idx - speed_offset]
                        y_speed = temp_dict['Y-Speed'][idx - speed_offset]
                    else:
                        speed = 'N/A\n'
                    if 0 <= idx - accel_offset < len(temp_dict['Acceleration']):
                        accel = temp_dict['Acceleration'][idx - accel_offset]
                        x_accel = temp_dict['X-Acceleration'][idx - accel_offset]
                        y_accel = temp_dict['Y-Acceleration'][idx - accel_offset]
                    else:
                        accel = 'N/A\n'

                    # Prepare multi-line strings
                    disp_str = f"{disp:.4g}\n(x:{x_disp:.4g}, y:{y_disp:.4g})"
                    speed_str = f"{speed:.4g}\n(x:{x_speed:.4g}, y:{y_speed:.4g})" if not isinstance(speed, str) else speed
                    accel_str = f"{accel:.4g}\n(x:{x_accel:.4g}, y:{y_accel:.4g})" if not isinstance(accel, str) else accel

                    notes_table.append([note, frame, time_from_trig, disp_str, speed_str, accel_str])
                
                notes_pdf_table = PDFSpreadsheet(self.pdf, 'Annotations', col_headers, col_widths)
                for row in notes_table:
                    notes_pdf_table.print_row(row)

            # movement graphs
            self.pdf.add_page()
            self.pdf.set_font('SansBold', '', 16)
            self.pdf.ln(20)
            self.pdf.cell(0, 10, f"Kinematic Graphical Output for {temp_dict['name']}", align='C')
            # displacement graph
            self.plot_graph(temp_dict['frame_ts'], (temp_dict['X-Displacement'], temp_dict['Y-Displacement'], temp_dict['Displacement']), 'disp', i, temp_dict['notes'], n_diff)
            # speed graph
            if len(temp_dict['Speed']) > 0:
                x = temp_dict['frame_ts'][n_diff - speed_offset:frames_length - speed_offset]
            else:
                x = []
            self.plot_graph(x, (temp_dict['X-Speed'], temp_dict['Y-Speed'], temp_dict['Speed']), 'speed', i, temp_dict['notes'], n_diff)
            # acceleration graph
            if len(temp_dict['Acceleration']) > 0:
                x = temp_dict['frame_ts'][(2 * n_diff) - accel_offset:frames_length - accel_offset]
            else:
                x = []
            self.plot_graph(x, (temp_dict['X-Acceleration'], temp_dict['Y-Acceleration'], temp_dict['Acceleration']), 'accel', i, temp_dict['notes'], n_diff)
            self.pdf.image(os.path.join(self.temp_dir, f'disp_{i}.png'), x=fpdf.enums.Align.C, y=40, h=80)
            self.pdf.image(os.path.join(self.temp_dir, f'speed_{i}.png'), x=fpdf.enums.Align.C, y=120, h=80)
            self.pdf.image(os.path.join(self.temp_dir, f'accel_{i}.png'), x=fpdf.enums.Align.C, y=200, h=80)

            # vibration graphs
            self.pdf.add_page()
            self.pdf.set_font('SansBold', '', 16)
            self.pdf.ln(20)
            self.pdf.cell(0, 10, f"Vibration Graphical Output for {temp_dict['name']}", align='C')
            (freqs, fft_mag, peaks) = temp_dict['X-Vibration']
            self.plot_graph(freqs, fft_mag, 'X-vib', i, temp_dict['notes'], n_diff, peaks)
            (freqs, fft_mag, peaks) = temp_dict['Y-Vibration']
            self.plot_graph(freqs, fft_mag, 'Y-vib', i, temp_dict['notes'], n_diff, peaks)
            self.pdf.image(os.path.join(self.temp_dir, f'X-vib_{i}.png'), x=fpdf.enums.Align.C, y=55, h=80)
            self.pdf.image(os.path.join(self.temp_dir, f'Y-vib_{i}.png'), x=fpdf.enums.Align.C, y=160, h=80)

        # create final pdf and cleanup
        self.pdf.output(report_path)
        self._delete_temp_files()


class PDFSpreadsheet:
    def __init__(self, pdf, title, headers, col_widths):
        self.pdf = pdf
        self.title = title
        self.col_widths = col_widths
        self.headers = headers
        self.line_height = 5
        self.print_table_header()

    def estimate_lines(self, text, col_width):
        words = text.split()
        lines = []
        current_line = ""
        for word in words:
            test_line = current_line + " " + word if current_line else word
            if self.pdf.get_string_width(test_line) < col_width:
                current_line = test_line
            else:
                lines.append(current_line)
                current_line = word
        if current_line:
            lines.append(current_line)
        return lines

    def print_table_header(self):
        if self.title:
            self.pdf.set_font('SansBold', '', 16)
            self.pdf.ln(5)
            self.pdf.cell(0, 12, self.title, align='C')
            self.pdf.ln(10)
        self.pdf.set_font('SansBold', '', 10)
        x_start = self.pdf.get_x()
        y_start = self.pdf.get_y()
        max_lines = 0
        for i, header in enumerate(self.headers):
            lines = self.estimate_lines(header, self.col_widths[i])
            max_lines = max(max_lines, len(lines))

        row_height = self.line_height * max_lines
        x = x_start
        for i, header in enumerate(self.headers):
            self.pdf.set_xy(x, y_start)
            self.pdf.multi_cell(self.col_widths[i], self.line_height, header, border=0, align='C')
            self.pdf.rect(x, y_start, self.col_widths[i], row_height)
            x += self.col_widths[i]
        self.pdf.set_y(y_start + row_height)
        self.pdf.set_font('DejaVuSans', '', 8)

    def print_row(self, row_data):
        self.pdf.set_font('DejaVuSans', '', 8)
        x_start = self.pdf.get_x()
        y_start = self.pdf.get_y()
        cell_lines = []
        max_lines = 0

        for i, text in enumerate(row_data):
            lines = self.estimate_lines(text, self.col_widths[i])
            cell_lines.append(lines)
            max_lines = max(max_lines, len(lines))

        row_height = self.line_height * max_lines

        if self.pdf.get_y() + row_height > self.pdf.page_break_trigger:
            self.pdf.add_page()
            self.pdf.ln(15)
            self.print_table_header()
            y_start = self.pdf.get_y()

        x = x_start
        for i, lines in enumerate(cell_lines):
            self.pdf.set_xy(x, y_start)
            self.pdf.multi_cell(self.col_widths[i], self.line_height, "\n".join(lines), border=0, align='L')
            self.pdf.rect(x, y_start, self.col_widths[i], row_height)
            x += self.col_widths[i]

        self.pdf.set_y(y_start + row_height)