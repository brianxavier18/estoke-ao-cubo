import os
from svglib.svglib import svg2rlg
from reportlab.graphics import renderPM
from PIL import Image
import io

def convert_icon():
    svg_path = os.path.join("icon", "icon-cubo.svg")
    ico_path = os.path.join("icon", "icon-cubo.ico")
    png_path = os.path.join("icon", "icon-cubo.png") # Fallback or intermediate

    try:
        image = None
        
        # Tentar carregar do SVG primeiro
        if os.path.exists(svg_path):
            print(f"Lendo SVG: {svg_path}")
            drawing = svg2rlg(svg_path)
            
            # Renderizar para PNG na memória com alta resolução
            # Escalar o desenho para garantir qualidade
            target_size = 1024
            scale_factor = target_size / max(drawing.width, drawing.height)
            drawing.scale(scale_factor, scale_factor)
            drawing.width *= scale_factor
            drawing.height *= scale_factor
            
            png_data = io.BytesIO()
            renderPM.drawToFile(drawing, png_data, fmt="PNG")
            png_data.seek(0)
            image = Image.open(png_data)
            
            # Salvar o PNG de alta resolução para uso no app (CTkImage)
            image.save(png_path, format="PNG")
            print(f"PNG de alta resolução salvo: {png_path}")
            
        # Se não tiver SVG, tenta o PNG existente
        elif os.path.exists(png_path):
            print(f"SVG não encontrado. Usando PNG: {png_path}")
            image = Image.open(png_path)
        
        else:
            print("Nenhum arquivo de ícone (SVG ou PNG) encontrado em 'icon/'.")
            return

        if image:
            # Garantir que é quadrado
            width, height = image.size
            if width != height:
                print(f"Redimensionando para quadrado (Original: {width}x{height})...")
                max_dim = max(width, height)
                new_img = Image.new("RGBA", (max_dim, max_dim), (0, 0, 0, 0))
                # Centralizar
                left = (max_dim - width) // 2
                top = (max_dim - height) // 2
                new_img.paste(image, (left, top))
                image = new_img
            
            # Salvar como ICO com múltiplos tamanhos para melhor qualidade no Windows
            icon_sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
            image.save(ico_path, format='ICO', sizes=icon_sizes)
            print(f"Ícone criado com sucesso: {ico_path}")
            print("Tamanhos incluídos: " + ", ".join([f"{s[0]}x{s[1]}" for s in icon_sizes]))

    except Exception as e:
        print(f"Erro ao converter ícone: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    convert_icon()
